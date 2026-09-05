# Human test plan — row P1-A2, the agent service skeleton

**Row:** master §3.1 stage 3, `P1-A2 [B]` · **Gate:** curl a session; journaled; dies with parent ·
**Met on 2026-09-01** (built, re-run by a reviewer, re-run by the verifier). No design source
(service internals). Your decisions applied: FastAPI + uvicorn in an `agent` extra, SSE as a plain
streaming response, the webview will talk to `127.0.0.1:<port>` directly (cradle's CSP is null),
the sidecar picks a free port and prints it as its only stdout line, and it dies with its parent.

What shipped, all under `src/canon/agent/`: `registry.py` (`Tool` + `ToolRegistry`, tiers as
strings), `permissions.py` (the shell: `auto` runs, `ask`/`paid` refused with a reason naming
row A4), `conversations.py` (`ConversationStore`, transcripts at
`<pack>/.canon/agent/<conversation>.jsonl`), `service.py` (the FastAPI app + `main()`),
`providers.py` (the registrar map shared with the eval runner); `canon agent serve` and
`python -m canon.agent.service` are the same entry. `run_conversation` gained `history` and
`on_message` hooks rather than a second loop. A conversation requires an open pack (the project
store arrives with P0-10).

## A. Integrity

- [ ] **A1 — Tests** (37 service tests incl. two real subprocess runs of the sidecar):

```bash
uv run python -m pytest tests/test_agent_service.py tests/test_agent_eval.py tests/test_chat_backends.py -q -p no:cacheprovider
```

- [ ] **A2 — The extra is isolated.** `pyproject.toml` has `agent = ["fastapi>=0.115", "uvicorn>=0.30"]` and the base dependencies are unchanged (`pydantic` only).

## B. The gate, by hand (five minutes, $0)

- [ ] **B1 — Make a pack and a one-turn fake script:**

```bash
uv run canon world new /tmp/a2_pack --seed 7 > /dev/null && printf '[[{"type":"text","text":"hello from the fake"}]]' > /tmp/a2_script.json
```

- [ ] **B2 — Start the sidecar under a helper shell that is its parent** (the port line is the first and only stdout line):

```bash
sh -c 'uv run python -m canon.agent.service --pack /tmp/a2_pack --backend fake --port 0 --parent-pid $$ --fake-script /tmp/a2_script.json > /tmp/a2.out 2> /tmp/a2.err & sleep 3; head -1 /tmp/a2.out; sleep 60' &
```

- [ ] **B3 — Curl a session** (substitute the port from B2):

```bash
PORT=$(head -1 /tmp/a2.out | python3 -c "import json,sys; print(json.load(sys.stdin)['port'])"); curl -s http://127.0.0.1:$PORT/health; echo; ID=$(curl -s -X POST http://127.0.0.1:$PORT/conversations -H 'content-type: application/json' -d '{"system":"You are Wick."}' | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])"); echo "conversation $ID"; curl -sN -X POST http://127.0.0.1:$PORT/conversations/$ID/messages -H 'content-type: application/json' -d '{"text":"hi"}'
```

      Expected: `/health` lists the 12 read tools; the POST streams `message_start`, `text_delta` ×2, `content_block_done`, `message_stop`, `done` frames.

- [ ] **B4 — Journaled.** The transcript exists under the pack and reads meta / user / assistant / turn_end:

```bash
curl -s http://127.0.0.1:$PORT/conversations/$ID | python3 -c "import json,sys; [print(l['type']) for l in json.load(sys.stdin)]"; ls /tmp/a2_pack/.canon/agent/
```

- [ ] **B5 — Dies with parent.** Wait for the helper's `sleep 60` to end (or kill the helper shell), then:

```bash
sleep 2; pgrep -f "canon.agent.service --pack /tmp/a2_pack" || echo "sidecar gone"
```

- [ ] **B6 — A second turn threads the first** (the fake echoes once its script is spent): POST another message and confirm the transcript now has two user lines and two assistant lines.

## C. Read the contract (5 minutes)

- [ ] `registry.py`: tiers are strings; `execute` refuses `ask`/`paid` with the A4 reason; unknown tools return a structured error the loop turns into an `is_error` result.
- [ ] `conversations.py`: append-only jsonl, `conv_<8 hex>` ids, `messages()` rebuilds a resendable history; the docstring says cost rows are A6's.
- [ ] `service.py`: SSE event names are the `ChatEvent.type` strings plus `tool_call` / `tool_result` / `done`; one turn at a time per conversation (409 otherwise); uvicorn logs go to stderr so stdout stays a one-line machine contract; SIGTERM exits 0; a busy port is a one-line JSON usage error.
- [ ] Vocabulary: "conversation" everywhere, never "session".

## D. Decisions (also listed in chat)

- [ ] The §3.1 system prompt (core law + pack context + UI state + specialist) is not assembled yet: today `POST /conversations` takes an optional `system` string. Default: the service assembles the core + pack-context layers when A4.5 lands the roster and skills loader; the create body's `system` stays as an override for tests.
- [ ] The actor string is `agent:<conversation>` until A4/A4.5 thread the `/<specialist>` suffix. Confirm.

## E. On approval

- [ ] Say "A2 approved". I flip A2 in master §3.1 and Phase 1 §8 with a Built summary.
