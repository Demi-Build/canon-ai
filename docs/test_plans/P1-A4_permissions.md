# Human test plan — row P1-A4, permission engine, write tier, actor threading

**Row:** master §3.1 stage 4, `P1-A4 [B]` · **Gates:** ask round-trip; a grant persists in its
project and provably not in a second; restore undoes. **All three met 2026-09-01**, re-run by a
reviewer over a real socket.

Grants live at `<pack>/.canon/agent/permissions.json`, service-owned and unreachable from any tool
(master S17). Write-tier tools are thin wrappers over the existing canon verbs, threading
`actor=agent:<conversation>/<specialist>` built by one module on each side (I6).

## A. Suites

```bash
uv run python -m pytest tests/test_agent_permissions.py tests/test_agent_service.py -q -p no:cacheprovider
```

## B. The ask round-trip, by hand (five minutes, $0)

```bash
uv run canon world new /tmp/a4pack --seed a4 >/dev/null && printf '[[{"type":"tool_use","name":"update_row","input":{"type":"enemy","id":"cinder_beetle","fields":{"hp":9}}}],[{"type":"text","text":"Cinder Beetle now has 9 hp."}]]' > /tmp/foreman.json && ls /tmp/a4pack/enemy/
```

Use whichever enemy id the pack actually generated. Start the sidecar and note the port:

```bash
uv run canon agent serve --pack /tmp/a4pack --backend fake --fake-script /tmp/foreman.json --port 0 --parent-pid $$
```

In a second terminal, send a message in Allow mode and watch the stream stop at the chip:

```bash
curl -N -X POST http://127.0.0.1:PORT/conversations/CONV/messages -H 'content-type: application/json' -d '{"text":"give the beetle more hp","mode":"allow"}'
```

- [ ] **B1** the stream halts on `permission_request` carrying everything the chip copy needs:
      the tool, the target phrased as "‹Specialist› wants to ‹verb› ‹target›", the tier, and
      whether Always allow is enabled in this mode.
- [ ] **B2** deciding `always` resumes the stream (`permission_decision`, `tool_result`, `done`)
      and writes the grant:

```bash
cat /tmp/a4pack/.canon/agent/permissions.json && tail -1 /tmp/a4pack/.canon/journal.jsonl | python3 -c "import json,sys; e=json.load(sys.stdin); print(e['actor'], e.get('session'), e['detail']['kind'])"
```

- [ ] **B3 — grants govern actions, not agents.** A second conversation running the same tool
      does not ask again.
- [ ] **B4 — and they are per project.** Repeat against a second pack: it asks. This is the
      gate's "provably not in a second".
- [ ] **B5 — Ask mode disables the middle button** with its reason, and a paid-tier tool refuses
      `always` outright. Paid is never Always-allowable.
- [ ] **B6 — reject** returns an `is_error` tool result naming the reason, and the turn continues
      rather than dying.
- [ ] **B7 — restore undoes.** After an accepted write, restore the artifact to its before-hash:
      the bytes revert and a `restore` event is journaled. Nothing is deleted.

## C. I6, one place builds actor strings

```bash
cd ~/Documents/projects/cradle && npx vitest run src/lib/actor.test.ts
```

- [ ] **C1** that test is the machine check: `src/lib/actor.ts` is the only builder, and a grep
      for a bare `"cradle:user"` or a template-literal `agent:` anywhere else in `src` is empty.
      Canon's side is `canon.agent.actors`.

## D. Decisions to confirm

- [ ] The specialist suffix is `foreman` until A4.5 threads real specialists; the actor is always
      `agent:<conversation>/<specialist>`. Confirm.
- [ ] Revoke and revoke-all endpoints exist now; the Settings pane that calls them is A6's
      component mounted on P0-12's screen. Confirm that split.

## E. On approval

- [ ] Say "A4 approved".
