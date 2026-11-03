# Human test plan — row P0-1, the P0 paper

**Row:** master §3.1 stage 1, `P0-1 [A]` · **Gate:** user reads + approves the additions ·
**Deliverable under test:** `docs/September_Phase_0_prd.md`, section
`# P0 paper — row 1 inventories & formats` (lines 520–2432 on 2026-09-01), plus the row-1 Status
cell on line 40. Nothing else in the PRD changed; the master was not touched.

A paper has no buttons, so this plan is a **review pass**: integrity checks you can run, a reading
order with one spot-check per subsection (so you verify the inventories against the code rather
than trusting them), and the decisions only you can make. Approving the paper approves every P.9
default that you do not strike.

---



## A. Integrity (5 minutes, all commands from `~/Documents/projects/canon-ai`)

- [x] **A1 — Only the paper was added.** The file is 2678 lines; deleting the paper leaves the
      original 765 lines with exactly one changed line (row 1's Status cell):

```bash
wc -l docs/September_Phase_0_prd.md
```

```bash
grep -n "^# " docs/September_Phase_0_prd.md
```

      Expected headings, in order: `# PRD — September Phase 0` (3), `# W1 —` (59), `# P0 paper —`
      (520), `# W2 —` (2433), `# W3 —` (2545), `# W4 →` (2660).

- [x] **A2 — Row-1 status cell** (line 40) ends with *"drafted 2026-09-01 — paper in the
      `# P0 paper` section (P.0–P.9) below; awaiting user approval"* and the rest of the row is
      unchanged.
- [x] **A3 — Master untouched.** `docs/September_master_prd.md` still says row P0-1 is pending
      (no ✅). It flips only after this approval.
- [x] **A4 — Subsection inventory.** P.0–P.9 all present:

```bash
grep -n "^## P\." docs/September_Phase_0_prd.md
```



## B. Reading order and spot-checks

Read the **contracts** in full (they are what later rows build against): P.0, P.3, P.4, P.7, P.8,
P.9 — about 900 lines. Skim the **inventories** (P.1, P.2, P.5, P.6) by their tables and run the
spot-check under each; every table row cites a `file:line`, so any row you doubt can be opened
directly. Path prefixes are defined in P.0 (`CA`, `MW`, `CR`, `DC`, `FX`).

### P.0 Scope & sources

- [x] The reading rule (master §6/§8 govern) and the open-vocabulary rule are stated up front.
- [x] The source table names a real file for every subsection (open two at random).



### P.1 Nine-schema field inventory (skim the nine tables)

- [x] **Spot-check the rename claim** — the NPC roll is `behavior_type` but lands on disk as
      `type` with engine class names:

```bash
grep -n "behavior_type\|_NPC_TYPE_MAP" examples/mazeworld_pack/specs.py examples/mazeworld_pack/parsers.py | head
```

- [x] **Spot-check a real emitted row** — confirm the npc keys in P.1.1's table exist and that
      `x`/`y` on the row are *not* where positions live:

```bash
python3 -c "import json;d=json.load(open('/Users/wolfgangblack/Documents/projects/MazeWorld/data_canon/npcs/npcs.json'));print(sorted(d[0].keys()))"
```

```bash
python3 -c "import json;d=json.load(open('/Users/wolfgangblack/Documents/projects/MazeWorld/data_canon/rooms/room_0/maze.json'));print(sorted(d.keys()));print('npc_positions',d['npc_positions'])"
```

- [x] **Spot-check "post hooks cannot dump to JSON"** (why monster/item mechanics are `code_fields`):

```bash
sed -n 185,196p src/canon/skeleton/loader.py
```

- [x] P.1.10's merged protected set and P.1.11's authoring plan read as one consistent story
      (class has no row spec; room/music/sfx are net-new).



### P.2 Condition-namespace inventory

- [x] **Spot-check the one fact everything rests on** — the engine walker reads only `text` and
      `next_node_id` from a choice; no conditions are evaluated today:

```bash
sed -n 225,240p /Users/wolfgangblack/Documents/projects/MazeWorld/src/utils/conversation_utils.py
```

- [x] **Spot-check the quest-state vocabulary** the engine actually has (P.2.1 `quest` row):

```bash
sed -n 30,38p /Users/wolfgangblack/Documents/projects/MazeWorld/src/models/quest.py
```

- [x] P.2.4's dungeon `evaluable_namespaces` block is explicit and empty except the selector-level
      `quest` states; the platformer carries none until `dialogue` is enabled.



### P.3 Shapes (read fully)

- [x] `EntityKind` marks seed-only vs stamped fields; `layout` is an object, never an enum.
- [x] `GridKind.placements` is a stamped shape naming the kind, wire key and journal kind.
- [x] `DialogueSpec` carries the five scopes and the full `operands` seed; the `dialogue test
      --state` payload is shown.
- [x] `PackSpec` lists the stamped subset explicitly.



### P.4 Registry format (read fully)

- [ ] P.4.1: the four-tier `resolve_pack` order and the `pack_type` mirror rule (manifest writers
      rebuild the file wholesale today — verify):

```bash
grep -n "pack_type" src/canon/pipeline/phases/manifest.py examples/platformer_pack/compose.py | head
```

      Expected: no hits — that is the gap the mirror rule closes.

- [x] P.4.3: the engines-block table is §5.1b's seven fields **plus two additive rows** marked
      "pending C1". Decide C1 in section C below.
- [x] P.4.5: the tuning block is `{"schema": "canon-tuning/v0", "status": "reserved", "keys": {}}`
      for every template in Phase 0; the six-key example is labelled illustrative. Verify the
      vocabulary it will seed from:

```bash
python3 -c "import json;print(list(json.load(open('examples/platformer_pack/rule_overrides.json'))['keys']))"
```

- [x] P.4.6: `pack info` output is one JSON document and cradle's `world_kind` is `pack_type`
      verbatim.



### P.5 Music / SFX row schemas (skim)

- [x] **Spot-check the engine contract** the rows must preserve — file stem == id, missing key is
      silence:

```bash
sed -n 100,116p /Users/wolfgangblack/Documents/projects/MazeWorld/src/systems/music_controller.py
```

- [x] The two skeleton files use only `choices` / `lookup` / `lookup_ranges` with **integer** bands
      (P.5.2 explains why sfx uses `duration_ms`).



### P.6 Room grid ↔ level editor (skim the mapping table, read P.6.5)

- [x] **Spot-check the cell vocabulary** in a real maze (expect only `-1, 0, 1` and item ids ≥ 2000):

```bash
python3 -c "import json;g=json.load(open('/Users/wolfgangblack/Documents/projects/MazeWorld/data_canon/rooms/room_0/maze.json'))['grid'];print(sorted({v for r in g for v in r}))"
```

- [ ] P.6.5 M7: the PRD promised "🎲 monsters" but the code places no monsters — decide G4 below.
- [ ] P.6.3a's room bundle keeps the platformer `LevelBundle` shape with `stage_id: ""`.



### P.7 `canon world update` (read fully)

- [ ] `manifest.movement/combat/rules` are excluded (§3.0-A) and the wall is a **parameter** of
      the shared core, not a constant.
- [ ] The field table lives in stamped `PackSpec.world_fields`; the `<list>[<key>=<value>]`
      address grammar never accepts a numeric index.
- [ ] P.7.4's `registry set` merge rule and P.7.5's `db define`/`db evolve` payloads are enough
      to build P0-6 without guessing.



### P.8 Journal / ledger (read fully — this is what P1-A6 implements)

- [ ] P.8.1 is verbatim today's shape. **Spot-check** that `record()` really has no cost fields
      and `gen` is free-form:

```bash
sed -n 160,192p src/canon/provenance.py
```

- [ ] P.8.2: the additive fields are exactly master §3.0-B's — `identity`, `costCents`,
      `accuracy` (`measured|estimated`), `genKind` (open), `batchId`, the `gen` inputs manifest,
      `accepted_tuning` + `pixel_edit` kinds; play sessions never journal.
- [ ] P.8.6: the eight one-line examples parse (paste one into `python3 -c "import json,sys;
      json.loads(sys.stdin.read())"` if you like) and each carries the fields P.8.2 promises.
- [ ] P.8.7: the journal is the one authoritative cost source; spend/jobs gain optional fields
      only; pre-A6 events read with defaults, never rewritten.
- [ ] P.8.8: the never-a-literal-union guidance names the Python, TypeScript and Rust rule.



## C. Decisions only you can make (P.9)

P.9 lists 57 lines; most are defaults you can accept by not striking them. These seven change a
master/doctrine text, a promised surface, or a design-package interaction, so decide them
explicitly (write your answer beside the item or tell me):

- [ ] **C1 — where engine evaluability lives.** Default: two additive, capability-gated fields on
      the registry engine entry (`evaluable_namespaces`, `evaluable_bindings`) and **amend master
      §3.0-H + Phase 0 §5.1b + §7.2** on approval so the field list agrees. Alternative: keep §5.1b
      exact and put the fields on `pack info` only.
- [ ] **R6 — engine entries on a platformer create.** Default: stamp **one** entry (`godot`,
      primary), leave the pygame harness on cradle's current `play_level` path until W2.0.
- [ ] **R14 (+ J8) — create-emitted deltas beyond** `bible.json`**.** Doctrine 7 says any further
      delta is "a new user decision". Default: exclude the whole `.canon/` directory from
      `tests/treediff.py` and sanction the one additive `manifest.json.pack_type` key.
- [ ] **G4 — "🎲 monsters" and monster dragging** in the P0-8 room editor. The code has no monster
      placement (monsters ride combat events). Default: drop both; monsters are edited on their
      combat event.
- [ ] **S7 — where scenes live.** Default: `events/events.json` as `type: "scene"`, shared id
      space; the engine loads unknown types as `CombatEvent`, so scenes never get an
      `event_positions` entry and the engine-lag layer warns.
- [ ] **C2 + C3 — quest states and the** `time:` **operand vs the design package.** Default: seed the
      engine's four quest states (design's `offered`/`turn-in` render unsupported) and use period
      names `dawn·day·dusk·night` for `time:` (the design's hour fields become prototype copy).
      Doctrine 9 gives design READMEs the interaction — say if you want the design's shapes kept
      and the engine gap carried as amber instead.
- [ ] **J6 — conversation-token cost rows as journal events** (`artifact_id: conversation:<id>`,
      `genKind: tokens`, hash-less) so the cost dashboard has one source. Alternative: spend-ledger
      only, two sources.

Everything else in P.9 (S1–S6, S8–S10, C4–C8, R1, R3–R5, R7–R8, R11–R13, G1–G3, G5, G7–G9,
A1–A2, A5–A8, J1–J5, J7): accept the default or strike the line. R9, R10 and A4 are already
resolved in the body and kept only for the record.

## D. On approval

- [ ] Tell me "P0-1 approved" (with any struck lines / decisions from section C). I will then:
      flip row 1 to ✅ in the Phase 0 build-order table with a 2–5 line Built summary, flip
      master row P0-1 the same way, apply the C1 amendments to master §3.0-H / Phase 0 §5.1b / §7.2
      if you took the default, and record the P.9 decisions in memory. I do not run git.

