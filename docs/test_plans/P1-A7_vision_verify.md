# Human test plan — row P1-A7, vision, the verify loop, and routing

**Row:** master §3.1 stage 5, `P1-A7 [B]` · **Gates:** the agent introduces a break, detects and
repairs it unprompted; a mixed request routes to the right specialists unaided. **Both met
2026-09-01**, and the verifier proved the first one against real files rather than a script.

What shipped: `src/canon/agent/tools_vision.py` (`capture_frames`, `run_trajectory`, `view_asset`,
all auto-tier per ASSUMPTION-6a and writing nothing into the pack), the mandatory post-mutation
verify folded into the run manager's existing one-retry rule, and a routing corpus that asserts
delegation order the same way the tool corpus asserts tool order.

## A. Suites

```bash
uv run python -m pytest tests/test_agent_vision.py tests/test_routing_eval.py -q -p no:cacheprovider
```

```bash
uv run python -m canon.agent.eval --backend fake
```

- [ ] **A1** the eval corpus is now eight conversations, all green at $0.

## B. The break-and-repair gate

```bash
uv run python -m pytest "tests/test_routing_eval.py::TestBreakAndRepair" -q -p no:cacheprovider
```

- [ ] **B1** the test runs the real write, read and validate tools on a generated pack: the level
      is genuinely invalid on disk after the agent's break, and genuinely valid after the repair,
      inside the same run. That is the difference between proving the loop and scripting a claim.
- [ ] **B2 — the verify is mandatory, not optional.** After any run that mutated something, the
      validation for what was touched runs automatically; one corrected retry is allowed, and a
      still-failing verify surfaces with an opinion rather than reporting done.

## C. Vision tools

- [ ] **C1 — they write nothing.** Frames are captured into a temp directory, attached, and
      deleted; the pack's hashes are identical before and after. The tests assert this on every
      tool.
- [ ] **C2 — a silent failure is impossible.** A harness run that produces no frames is a named
      error rather than an empty pass, and the environment is scrubbed to the `PLAT_*` variables
      the harness itself sets.
- [ ] **C3 — the honest caveat is documented where you will hit it:** a trajectory is
      position-only and structurally cannot see a rendering difference. A rendering question needs
      frames. That single fact once cost an entire debugging arc.
- [ ] **C4 — the escape hatch.** ASSUMPTION-6a lets you demote the two headless tools from auto to
      ask with a setting rather than a code change. It lives at `<pack>/.canon/agent/settings.json`
      under `tool_tiers`, which is the agent's own durable directory; it was moved there after a
      review found the registry file would overwrite it on the next registry write.

## D. Routing

- [ ] **D1** a mixed design-and-art request delegates to both the level designer and the artist;
      an art-only request never touches the level designer; a pure question delegates to nobody. A
      wrong delegation is a named failure.
- [ ] **D2 — the corpus uses the shipped foreman.** After a review finding, it assembles the real
      `roster/core.md` and `foreman.md` rather than a bespoke prompt, so the provider-swapped run
      at stage 6 measures the foreman you actually ship.

## E. Decisions to confirm

- [ ] Real routing quality can only be measured on a real model, which is a paid user-run leg. The
      fake corpus proves the machinery and the expectations; you run the paid pass when you want
      the quality number. Confirm that split.

## F. On approval

- [ ] Say "A7 approved".
