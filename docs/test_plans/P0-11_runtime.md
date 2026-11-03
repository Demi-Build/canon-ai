# Human test plan — row P0-11, the vendored runtime

**Row:** master §3.1 stage 7, `P0-11 [A2]` · **Gate:** a fresh machine with no Python installs the
app and creates a free world. **The macOS leg is proven here; two legs need you** (see section D).
Built to your decision of 2026-09-01: prove macOS locally, write the three-platform CI, hand you
the push.

What shipped: `scripts/fetch-runtime.sh` fetching python-build-standalone against a committed
checksum manifest pinning all five target triples, installing the canon wheel with the decided
extras `[cli, platformer, play, anthropic, images, audio, agent, openai]`; the runtime wired into
the bundle; one resolver honouring `CANON_BIN` then the bundled runtime then PATH at every spawn
site; a startup probe with a guided failure screen; a tightened asset protocol scope; a generated
third-party notices file; and `release.yml`'s existing three-platform matrix extended.

## A. The fetch and the runtime

```bash
cd ~/Documents/projects/cradle && bash scripts/fetch-runtime.sh
```

- [ ] **A1** it is idempotent: a second run with the payload present says so and does nothing. The
      tree is about 306 MB under `src-tauri/resources/runtime/`.
- [ ] **A2 — the bundled interpreter works with no checkout in sight.** From a directory outside
      both repos, with `CANON_BIN` unset:

```bash
cd /tmp && ~/Documents/projects/cradle/src-tauri/resources/runtime/aarch64-apple-darwin/python/bin/python -m canon.cli.main --version
```

- [ ] **A3 — a bad checksum fails by name** and leaves no half-written tree. Worth trying once by
      corrupting a line in the manifest on a scratch copy.
- [ ] **A4 — `cargo check` passes with and without the payload present**, so a fresh clone builds.

## B. Resolution and first run

- [ ] **B1** the resolution order is `CANON_BIN` first for developers, then the bundled runtime,
      then PATH. Your dev workflow is unchanged.
- [ ] **B2** every spawn site goes through that one resolver: canon verbs, the play harness, and
      the agent sidecar. A bundled app has no repo to derive an interpreter from.
- [ ] **B3 — the startup probe.** Point `CANON_BIN` at a nonexistent path and launch: you should
      get a screen saying what was tried, in what order, and what to do, rather than a raw file-not-
      found. Restore the variable afterwards.
- [ ] **B4 — the asset protocol scope** is narrower than the previous wildcard and still opens the
      bundled demo and a folder you pick yourself.

## C. Licensing

- [ ] **C1** the generated notices cover the Python runtime, every wheel inside it, and the Rust
      crates, and they state how a user replaces pygame. That replaceability is the obligation the
      LGPL posture rests on, so it is worth reading once rather than assuming.

## D. What needs you

These are the gate legs I cannot prove from this machine.

- [ ] **D1 — the three-platform build.** `release.yml`'s matrix covers macOS, Ubuntu and Windows,
      and the manifest pins a checksum for all five triples, but the workflow is tag-triggered and
      CI runs on your push. Push the branch, then a release tag, and confirm all three jobs go
      green and that the runtime actually lands inside each bundle.
- [ ] **D2 — a genuinely Python-free machine.** Install the resulting build on a machine with no
      Python and create a free world. That is the literal gate sentence.
- [ ] **D3 — signing and notarization** run in CI with your Apple credentials, which I never touch.

## E. Decisions to confirm

- [ ] The bundle now carries the `agent` and `openai` extras per your decision, which is what keeps
      the panel alive in a shipped app. Torch extras stay excluded, so the local image backend is
      not in the bundle.

## F. On approval

- [ ] Say "P0-11 approved for the local legs" once A and B pass; the row only fully closes after D1
      and D2, which is honest rather than pedantic.
