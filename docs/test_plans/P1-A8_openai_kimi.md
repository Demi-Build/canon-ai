# Human test plan — row P1-A8, openai + kimi chat backends (begun)

**Row:** master §3.1 stage 2, `P1-A8 [B]` · **Gate:** "A1–A7 eval passes provider-swapped" —
closes at **stage 6**, after A7. This plan covers what stage 2 delivers: the implementations
exist, are hermetically tested, and are wired into the eval runner. Nothing here needs a key or
money except section D, which is optional and user-run.

What shipped: `src/canon/backends/chat_openai.py` — one `OpenAIChatBackend` on the Chat
Completions API, registered explicitly as `openai` (`register()`) and `kimi` (`register_kimi()`,
Moonshot base URL `https://api.moonshot.ai/v1`, `MOONSHOT_API_KEY`); the `openai` extra
(`openai>=1.58`); the eval runner's registrar map; `tests/test_chat_openai.py` (≈75 tests incl. a
loop-level dry run of all five scripted conversations over the OpenAI shape). No pricing anywhere.

## A. Integrity

- [ ] **A1 — Files.** New: `src/canon/backends/chat_openai.py`, `tests/test_chat_openai.py`. Extended: `src/canon/backends/__init__.py` (lazy export), `src/canon/agent/eval.py` (registrar map), `tests/test_agent_eval.py`, `pyproject.toml` + `uv.lock` (the `openai` extra only).
- [ ] **A2 — No pricing in the row:**

```bash
uv run python -m pytest tests/test_chat_backends.py tests/test_chat_openai.py -q -k "no_pricing or no_cost" -p no:cacheprovider
```

## B. Hermetic proof

- [ ] **B1 — Tests:**

```bash
uv run python -m pytest tests/test_chat_openai.py tests/test_chat_backends.py tests/test_agent_eval.py -q -p no:cacheprovider
```

- [ ] **B2 — The fake gate is untouched:**

```bash
uv run python -m canon.agent.eval --backend fake
```

- [ ] **B3 — Keyless real backends fail by name, never a traceback** (exit 1; each line says `no credential — set OPENAI_API_KEY` / `MOONSHOT_API_KEY`):

```bash
env -u OPENAI_API_KEY -u MOONSHOT_API_KEY uv run python -m canon.agent.eval --backend openai; echo "exit=$?"
```

```bash
env -u OPENAI_API_KEY -u MOONSHOT_API_KEY uv run python -m canon.agent.eval --backend kimi; echo "exit=$?"
```

## C. Read the mapping (5 minutes, `src/canon/backends/chat_openai.py`)

- [ ] Tool results for one assistant turn are exploded into consecutive `role: tool` messages in order (the neutral shape keeps them in one user message; the loop is provider-agnostic).
- [ ] `finish_reason` maps `tool_calls→tool_use`, `stop→end_turn`, `length→max_tokens`, `content_filter→refusal`; tool_use wins whenever tool blocks are present.
- [ ] Usage: `input_tokens = prompt_tokens − cached_tokens`; Kimi reports `cached_tokens` at the top level and that is read too.
- [ ] Kimi thinking models: `reasoning_content` is echoed back on replay, and thinking is sent as disabled unless the backend is constructed with `reasoning=True` (see decisions).
- [ ] Cancel: closing the generator closes the SDK stream.

## D. Optional paid leg — USER-RUN, real money, not part of this stage's gate

Smallest conversation, wording check freed, tool order strict:

```bash
OPENAI_API_KEY=... uv run python -m canon.agent.eval --backend openai --only just-talking
```

```bash
MOONSHOT_API_KEY=... uv run python -m canon.agent.eval --backend kimi --only just-talking
```

Expected: PASS lines with a cost note of the form `measured tokens in=…/out=… (cache read=…); priced by the §3.0-C module from P0-7`, no dollar figure. A tool-using conversation (`--only unbeatable-level`) proves the tool round-trip on a real model; a real model may word things differently, which is fine.

## E. Decisions (also listed in chat)

- [ ] Default ids: `gpt-5.1` (openai), `kimi-k2.6` (kimi).
- [ ] `effort` is forwarded as `reasoning_effort` only when the backend is constructed with `reasoning=True`; the same flag keeps Kimi thinking on. `kimi-k3` and `kimi-k2.7-code` need `reasoning=True`; the eval CLI has no `--reasoning` flag yet.
- [ ] When `content_filter` arrives together with tool calls, tool_use wins and no `stop_details` are attached.
- [ ] The venv incident: `uv sync --extra openai` is an exact sync and briefly removed every other optional package; they were restored (anthropic, pytest, ruff, numpy, Pillow, pygame, fal-client, google-genai, elevenlabs, typer). `torch`/`diffusers`/`transformers` are NOT installed now — say if they were before and you want them back.

## F. On approval of this stage's slice

- [ ] Say "A8 slice approved". The row stays open in the master until stage 6; I record the slice as landed in Phase 1 §8.
