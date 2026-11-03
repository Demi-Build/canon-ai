# Provenance & Traceability Spec

**Status:** v1 partially implemented (generate + edit instrumented; object store live).
**Purpose:** capture the full *trajectory* of a pack — what canon generated, what a
user changed, kept, deleted, imported, or swapped — as an append-only, content-
addressed record. This is (a) the substrate for **training data** (generated →
human-corrected pairs) and (b) the basis for **telemetry** when cradle becomes a
webapp (API-call accounting + change tracking).

The governing principle: **every mutation flows through a canon write verb, and
each verb records here.** Because cradle shells out to canon for all writes, one
choke point gives complete coverage — in the desktop app today and a webapp later.

---

## 1. Two layers

| Layer | Owns | Where |
|---|---|---|
| **State + lineage** (canon Bible / artifacts) | what exists, how it was made, what depends on what | `provenance_hash`, `parents`, `status`, `review_status` on every artifact; `GenerationTrail` (prompt/response/retry/cost); `generation_stats.json` |
| **Trajectory** (this spec) | the ordered stream of *mutations* + the bytes of every version | `.canon/journal.jsonl` + `.canon/objects/<sha256>` |

They join on `artifact_id` + content hash. The state layer answers "what is this and
what did it derive from"; the journal answers "what happened to it, when, by whom."

Content-hashing makes **"kept vs changed" a free comparison**, and the object store
makes any version fully reconstructable.

---

## 2. The journal — `.canon/journal.jsonl`

Append-only, one JSON event per line. Outside canon's byte-determinism contract.

```jsonc
{ "schema": 1, "ts": "<UTC ISO-8601>",
  "artifact_id": "level:ember_grove/l1/entities",
  "op": "generate | edit | keep | delete | import | switch | regenerate",
  "source": "llm | user | import",
  "actor": "cradle:user",            // who acted (user id in a webapp)
  "session": "sess-abc",             // optional grouping
  "detail": { "kind": "enemy_move", "moves": [ { "id": "cinder_beetle", "from": [19,13], "to": [10,8] } ] },
  "before_hash": "sha256:…",         // CAS ref to the prior version (null for generate)
  "after_hash":  "sha256:…",         // CAS ref to the resulting version
  "gen": { "model": "...", "prompt_hash": "...", "input_tokens": 0, "output_tokens": 0, "cost": 0.0 } // generate/regenerate only
}
```

### Op taxonomy → training label
| op | meaning | training signal |
|---|---|---|
| `generate` | canon produced the artifact (baseline import) | the reference output |
| `keep` | user approved unchanged (`review_status` → approved, hash unchanged) | **positive** example |
| `edit` | user changed it | **(generated → human) correction / preference pair** |
| `switch` | user repointed a reference to a different existing artifact | **rejection + preferred alternative** |
| `import` | user brought in external bytes (upload) | **rejection + external target** |
| `delete` | user removed it | **negative** example |
| `regenerate` | user re-ran canon on it | **iteration pair** (old vs new + reason), carries `gen` |

The gold pair is `edit` (and `regenerate`): `read_object(before_hash)` vs
`read_object(after_hash)` reconstructs exactly what changed.

Artifact-id families beyond the bible's own (`enemy:<id>`, `tileset:<stage>`,
`level:<stage>/<id>/<step>` …): `schema:<type>` — the pack-local roll-table
override written by `canon db schema --set` (a user edit to the *distribution
bounding generation*, not to any one row; `detail.kind = "db_schema"`).

---

## 3. The object store — `.canon/objects/<sha256>`

Content-addressed (git-style): each version's exact bytes stored once, keyed by
sha256. Identical content dedups. `before_hash`/`after_hash` in journal events point
here, so **full version history is replayable** without keeping per-edit copies in
the tree. `snapshot_file` on write; `read_object` to fetch.

**Append-only, never pruned by user actions.** A user "delete" (§7) is a *state +
journal* change; the bytes and the delete event are retained. The object store is
the durable record — it is only ever added to.

---

## 3a. Two audiences, one store — read tiers

The journal + object store is a single source with two read policies:

| Tier | Sees | Purpose |
|---|---|---|
| **User** (every cradle user) | *their own* artifacts' version chains (`artifact_versions`), restore-to-any-version | product feature: recover the original or any edit of a map/asset |
| **Internal** (cradle devs + Demi research) | the *full* journal — every event across users, incl. deletes, `actor`, `gen` (prompts/models/cost), cross-artifact | the LLM/prompt **training corpus** |

The split is a **read policy, not a data fork** — never duplicate or diverge the
store. In the local desktop app the two tiers collapse (single user = the dev). In
the webapp they are enforced at the API/service boundary; the internal corpus is
never exposed through the user-facing surface.

---

## 7. Retention & deletion

- **Restore / revert** (user-facing, built): `level versions` lists an artifact's
  chain; `level restore --to <hash>` writes any stored version back through the
  normal edit path, journalled as `op:"restore"`. The version left behind stays in
  the store — reverting is itself a training signal ("edits rejected → original").
- **Delete is soft + retained** (design; lands with cradle's delete action): a user
  delete emits `op:"delete"` and removes the artifact from *their working set*
  (won't render/export), but **never** erases the object-store bytes or the journal
  events. "Even if the user deletes it, we keep it for training" is enforced by the
  append-only store + the read-tier split — the user stops seeing it; the internal
  corpus keeps it (as a negative example).
- **No user-triggered GC.** Object-store pruning, if ever needed, is an internal
  retention-policy job (bounded by consent/PII rules), never a user delete.

---

## 4. What's implemented (v1)

`src/canon/provenance.py` — journal + CAS primitives (`snapshot_file`, `record`,
`read_object`, `already_recorded`, `append_event`).

Instrumented write verbs (`src/canon/adapters/platformer_write.py`, exposed on the
`canon level` CLI):
- **`baseline`** → `op: generate` for each of a level's step artifacts (collision,
  terrain, background, hazards, triggers, foreground, entities, items, level).
  Idempotent (dedup by artifact_id + hash). Cradle calls this on level import.
- **`apply-edit`** → `op: edit` with before/after snapshots + a semantic `detail`
  diff (enemy/item moves, marker moves, sparse-mask counts). Stamps `user_edited`.
- **`history`** → dump the journal (optionally per level) for inspection / a future
  cradle History panel.
- **`versions`** → the version chain for a step artifact (the restore picker's source).
- **`restore`** → revert a step to any stored version (`op:"restore"`); the version
  left behind is retained. Powers the user-facing "use the original or any edit."

Cradle wiring: `baseline_level` + `save_level_edit` Tauri commands shell out with
`--actor`; `LevelDetail` baselines on import and edits carry `actor: cradle:user`.

---

## 5. Not yet built (the roadmap this schema anticipates)

- **`regenerate`** events — emitted when cradle triggers `canon regen` per artifact,
  carrying the `gen` block (model/prompt_hash/tokens/cost) from `GenerationTrail`.
- **`keep` / `delete` / `import` / `switch`** — as cradle grows approve, delete,
  upload-asset, and switch-reference actions (each new verb records here).
- **Grid edits** — terrain paint via `canon level import-grids` records an `edit`
  with a cell-diff `detail` (same pattern, `.npz` snapshotted to the object store).
- **Per-call API telemetry** — surface each model call (`GenerationTrail` +
  `retry.py`) into the journal as `gen` records: model, tokens, cost, prompt_hash,
  retry number. `generation_stats.json` stays the aggregate.
- **Remote sink** — a `RemoteJournal` that flushes the *same* event shape to a
  telemetry endpoint for the webapp. Local-first stays the default.

---

## 6. Webapp / privacy gate

The event schema is identical for local and remote, so no rewrite is needed to go
hosted. **But** events carry user content and (in a webapp) user identity — a hosted
training pipeline needs explicit **consent + a PII/anonymization pass** before events
leave the machine. This is a product/legal decision that shapes the schema (e.g.
content-hash-only vs. full-value capture, per-user opt-in) and must be settled before
any remote sink ships.

**Highest-sensitivity case:** retaining content the user *deleted* (§7) for the
internal training corpus. Keeping user-created content the user chose to remove is
exactly the kind of collection that requires clear, up-front consent (ToS/opt-in) and
a documented retention policy. The local-first default keeps everything on the user's
machine; only the **remote collection** step — not the local store — is gated on
consent. Ship the consent flow before the `RemoteJournal` sink, not after.
