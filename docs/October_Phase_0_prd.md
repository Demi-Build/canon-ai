# PRD — October Phase 0: Cradle, the Cursor Way

**Status:** first pass, 2026-08-27, split out of `September_Phase_0_prd.md`
(where it began as workstream W4). Decisions below were taken in the same
review cycle.

**The goal:** transform cradle into something much closer to **Cursor**. The
editor runs locally (desktop today, browser later); the **agentic /
generative backend runs as a Demi-hosted service users call** — the same way
Cursor's editor works on local files while routing its model calls through
Cursor's backend. September Phase 0 builds the editing + packaging
foundation; this phase puts the backend online.

---

## 1. Decisions (2026-08-27)

1. **September writes no web code.** Its obligation is the eight anti-lock-in
   invariants (§4), applied in review, so nothing that phase builds deepens
   desktop coupling.
2. **First hosted editor milestone: single-tenant** — full read/edit/generate
   in the browser, one user per workspace. No interim view-only product.
3. **Real accounts from the start of hosting** (OAuth; account identity is
   the `--actor` on every journaled mutation).
4. **Managed cloud service, run by Demi** — the product-company path. The
   desktop app continues as the local-first sibling: same frontend, two
   transports.

## 2. The two planes — why the Cursor shape is cheap here

"Run the backend online, run the editor locally or in a web app" decomposes
along a different axis than desktop-vs-web. Cradle+canon has two planes:

- **Workspace plane** — the pack on disk plus the verbs that read, validate,
  journal, and write it. Cheap, deterministic, filesystem-rooted. Whichever
  side owns the pack must own these verbs; shipping packs per-verb or
  file-syncing them is the one architecture to avoid.
- **Generation plane** — the LLM / image / music / VLM calls. **Already
  remote today**: canon is a local orchestrator calling Anthropic, fal,
  ElevenLabs, Lyria. The pluggable backend Protocols (`LLMBackend`,
  `ImageBackend`, …) are the seam.

**The Cursor configuration = editor local, pack local, generation through
Demi's cloud.** In canon it is literally a new backend id
(`--llm-backend demi`, etc.) implementing the existing Protocols against
Demi's API with an account token: zero verb changes, zero pack changes,
works with the September desktop app on day one.

Orientation matrix: {editor: desktop | browser} × {workspace: local |
cloud}, with the gateway serving every cell. Cursor-mode is
(desktop, local) + gateway; the hosted editor is (browser, cloud) + gateway;
a desktop editor pointed at a cloud workspace is (desktop, cloud) — same API
facade, no extra work.

## 3. M0 — the generation gateway (this phase's centerpiece)

A Demi-hosted service that executes generation calls on behalf of signed-in
users:

- **Canon side:** `demi` implementations of the backend Protocols. Backend
  ids are already config/data, so the gateway appears as an id, not a
  refactor. (September carries one small obligation here: backend-selection
  UI treats ids as data from the pack/registry, never a hardcoded union.)
- **Service side:** authenticated proxy → provider APIs, with per-account
  metering. The spend ledger's shape (`op / backend / model / units / cost`)
  becomes the billing record.
- **Key custody, solved the product way:** users sign in; Demi holds the
  provider keys. Bring-your-own-keys (September's OS-keychain path) remains
  a first-class alternative — the Settings Keys pane gains one "Demi
  account" row beside the per-provider rows.
- **Later:** Demi-owned or fine-tuned models slot in behind the same ids
  (the plugin-API ambition in cradle's README).
- **What M0 does NOT touch:** packs, verbs, the journal, the editor's write
  path. It is monetizable and useful with the desktop app alone.

## 4. Anti-lock-in invariants (binding on September's W1–W3 work)

- **I1 — One IPC seam.** All frontend↔backend traffic goes through
  `invoke.ts`'s `api` object (true today: zero direct `invoke()` calls
  elsewhere; stays true).
- **I2 — Adapterize on touch.** No new `@tauri-apps` imports outside a small
  adapter set (`assetUrl`, `pickFolder`, `events`); files touched by
  September work move their direct imports behind the adapters. No big-bang
  migration.
- **I3 — Stateless commands.** Every new Tauri command takes the pack path +
  JSON and holds no in-process world state — what makes the HTTP facade a
  mechanical port of `lib.rs`.
- **I4 — Wheel-clean canon.** New canon capability is reachable from a plain
  wheel install (September W3.2's acceptance test is the gate).
- **I5 — Pack-resident truth.** Jobs, spend, provenance stay file-journaled
  under `<pack>/.canon/` so a server reconstructs everything from the
  workspace; no new frontend-owned durable state.
- **I6 — Actor threading.** Cradle centralizes the actor string in one
  module (retiring the 25 hardcoded `cradle:user` sites opportunistically);
  every new verb takes `--actor`. Account identity becomes a one-site
  change.
- **I7 — devMock parity.** New commands get devMock implementations — the
  standing proof the UI runs without Tauri, and the shape of the future HTTP
  client.
- **I8 — localStorage is per-device convenience only.** Durable state
  (recents → the project store) lives server-compatibly.

## 5. M1 — the hosted editor (single-tenant)

- **Same React frontend**; the transport behind `api` swaps Tauri `invoke`
  for HTTP + SSE. devMock's `installDevMock()` is the working template.
- **API service** mirroring the command surface 1:1 (mechanical port of
  `lib.rs`), invoking wheel-installed canon per workspace; per-workspace
  serial job queue first (identical semantics to desktop), Celery-style
  horizontal scale later (the `cradle-jobs/v1` ledger is already
  backend-agnostic).
- **Workspace = server-side pack dir** — the September project store concept
  maps 1:1. Accounts via OAuth (GitHub first for the dev audience);
  membership = access control; account id = `--actor`.
- **Assets** over an authenticated endpoint keyed by `resolveAsset` hints;
  `replaceAsset` becomes a multipart upload. **Progress** via SSE relaying
  `.canon/log.jsonl` — the same file-poll mechanism the desktop uses.
- **Generation** flows through M0's gateway (per-workspace metering).
- **Known degradation:** pygame/Godot playtest stays desktop-only at M1;
  server-side capture/streaming is a later milestone.

## 6. Milestone ladder

- **M0 — Generation gateway** (the Cursor step): backend proxy + `demi`
  backend ids + account sign-in in Settings. Ships against the desktop app;
  independent of the workspace plane.
- **M1 — Single-tenant hosted editor:** API facade + HTTP transport +
  accounts + cloud workspaces on managed infra.
- **M2 — Teams:** workspace membership, presence, per-workspace write
  serialization (verb-sized transactions through the queue), attribution
  surfaced in History.
- **M3 — Live collaboration:** concurrent editing sessions on the
  journal+CAS substrate. Designed only once M2 has real users.

## 7. Risks & open questions

- **Ops/billing/security burden** of the managed path — accepted with
  decision 4; M0 keeps its first slice small (a proxy, not a platform).
- **Key/vault design and metering accuracy** at M0 — the spend ledger's
  estimates vs provider-billed actuals need reconciliation.
- **Gateway abuse/limits** — per-account rate + spend caps from day one.
- **Playtest degradation** at M1 (named above).
- **Pricing model** — unpriced; not needed before M0 ships to testers.
