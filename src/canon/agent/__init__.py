"""canon.agent — the agent service home (Phase 1 PRD §6, "Agent service module").

Row A1 lands the minimum that proves the chat contract end to end at $0:

- ``loop`` — ``run_conversation``, the single-actor tool-use loop driver
  (request → stream → assistant turn → tool results in ONE user message →
  repeat until the model stops, bounded by ``max_tool_rounds``).
- ``evals`` — ``ScriptedConversation`` and the built-in ``CONVERSATIONS``:
  the scripted tool-use conversations the ``FakeChatBackend`` plays.
- ``eval`` — ``run_scripted`` + ``main`` (``python -m canon.agent.eval``),
  the gate: every scripted conversation green on the fake, and later the
  provider-swap gate (row A8) and routing eval (row A7) on real backends.

Row A2 adds the service skeleton around that loop:

- ``registry`` — ``Tool`` + ``ToolRegistry``: the tools a conversation may
  call, in registration order, executed through the permission check.
  Row A3 registers the real read tools here.
- ``permissions`` — ``PermissionEngine`` SHELL: auto allowed, ask/paid
  refused with the reason naming row A4 (which brings the engine + the
  grants file).
- ``conversations`` — ``ConversationStore``: append-only transcripts at
  ``<pack>/.canon/agent/<conversation>.jsonl`` (the "journaled" of A2's
  gate; the cost journal is A6's).
- ``providers`` — the chat-provider registrar map, moved out of ``eval``
  so the eval runner and the service resolve backend ids identically.
- ``service`` — the FastAPI app + the sidecar entry
  (``python -m canon.agent.service`` / ``canon agent serve``): HTTP+SSE,
  a free port printed as the first stdout line, a parent-pid watchdog so
  it dies with cradle. Needs the ``agent`` extra (fastapi + uvicorn);
  nothing else in this package imports it.

Row A3 fills the registry's read side:

- ``tools_read`` — ``register_read_tools(registry, pack_dir)``: the
  auto-tier tools (``describe_pack``, ``describe_level``, windowed
  ``export_level``, ``validate_level``, ``db_types`` / ``db_schema`` /
  ``db_row``, ``get_history`` / ``get_versions``, the path-guarded
  ``list_pack_files`` / ``read_pack_file`` / ``search_pack``) — each an
  in-process wrapper over a canon verb, inputs validated against its
  JSON schema, results compact JSON strings. Zero writes.

Row A4 fills the write side and the gate:

- ``actors`` — ``agent_actor(conversation, specialist)`` / ``user_actor``
  / ``parse_actor``: the ONE place the ``agent:<conversation>/<specialist>``
  string is built (I6), plus ``bind_call`` / ``current_call`` — the call
  context the service binds around ``registry.execute`` so write tools
  attribute every verb.
- ``permissions`` — the real ``PermissionEngine``: tier × mode × project
  grants (``GrantStore`` at ``<pack>/.canon/agent/permissions.json``) and
  the blocking ask round-trip (``permission_request`` on the stream,
  ``decide`` from ``POST /conversations/{id}/permissions``).
- ``tools_write`` — ``register_write_tools(registry, pack_dir, actor_for=…)``:
  the ask-tier §4.B tools (``apply_level_edit``, ``import_level_grids``,
  ``create_level``, ``publish_level``, ``edit_world_map``, ``update_row``,
  ``update_schema``, ``pin`` / ``unpin``, ``restore``) — thin wrappers over
  the existing canon verbs, called with ``actor`` + ``session``.

Row A4.5 brings the specialist layer, plans and ⏹ Stop — all of it
EXTENDING ``loop.py`` (the cancel flag and the parallel predicate slot
into ``run_conversation``'s existing points; nothing writes a second loop):

- ``roster/`` — specialists as DATA: ``<id>.json`` (allowlist, model
  tier, actor) + ``<id>.md`` (role prompt) per specialist, ``core.md``
  the shared law; ``load_roster`` / ``resolve_model`` (the ``models.json``
  tiers precedent).
- ``skills`` — ``load_skills(pack_dir, project_store_dir)``: instruction
  skills (markdown + JSON front-matter) and recipes (fail-closed JSON),
  project-local over store over template; allowlists intersect, never
  widen; recipe tools are never Always-allowable.
- ``prompt`` — ``assemble``: the four §3.1 layers (core law · pack
  context · UI state · specialist role + matched skills).
- ``runs`` — ``RunManager``: ``delegate`` (specialist runs, parallel cap
  3, the per-pack write gate keyed by target, run lifecycle events),
  ``propose_plan`` (blocking approval, one tool call per step, ``batchId``
  on every write, halt on failure, reverse-order undo), ⏹ Stop for the
  conversation and per run.
- ``tools_play`` — ``sandbox_level(level_id?, spawn?)`` (ask tier) over
  the extended ``canon level sandbox`` verb; launching stays the play path.
- ``providers.list_models`` — the data behind ``GET /models``.
- ``service`` grows the endpoints: ``/models``, ``/roster``, ``/skills``,
  ``…/prompt``, ``…/stop``, ``/runs…``, ``…/plans…``.

Row A7 gives the agent EYES and makes it check its own work:

- ``tools_vision`` — ``register_vision_tools(registry, pack_dir)``: the
  auto-tier ``capture_frames`` / ``run_trajectory`` (the headless pygame
  harness under ``PLAT_CAPTURE`` / ``PLAT_TRAJ``, env-scrubbed, windowless,
  writing nothing into the pack — ASSUMPTION-6a, demotable to ask by a
  ``.canon/registry.json`` flag) and ``view_asset`` (an asset's bytes as an
  image attachment + its metadata).
- Images ride the canonical ``image`` block ``loop`` now passes through as
  tool_result CONTENT; the TRANSCRIPT stores a reference (path + sha256),
  so a picture is never re-sent on replay (§3.4).
- ``runs`` grows the VERIFY LOOP: after a run that mutated something the
  manager runs the mandatory validation itself, spends the run's one
  corrected retry on a failure, and returns a structured failure rather
  than a claim of done.
- ``evals`` / ``eval`` grow the ROUTING corpus + the ``expected_delegations``
  contract — the foreman's tool choice, strict on every backend.

Row A7.5 makes ``game_coder`` real — code in the project's OWN engine copy:

- ``tools_code`` — ``register_code_tools(registry, pack_dir, actor_for=…)``:
  ``engine_status`` (auto), ``engine_sync`` (ask, never Always-allowable)
  and ``edit_project_code`` (ask) over ``canon.engine_ops`` — the wall
  (canon's source, the shared template, another pack and everything outside
  ``godot/**`` refused by name), a unified diff that must apply cleanly, the
  ``modified`` stamp that makes ``engine sync`` refuse the file, and the
  ``code:<path>`` journal event ``restore`` undoes in one click.
- ``gates`` — the §7.1 GATE LADDER: syntax, headless boot (the
  ``SCRIPT ERROR`` count, because Godot's exit code lies), a scripted smoke
  through the ``PLAT_*`` mirror verified by the trajectory it produced, and
  ``validate_level`` on the affected levels. It runs as a LEG of A7's verify
  loop, never as a second verification path, and reports the engine rungs
  ``unproven`` — never green — on a machine with no Godot.
- ``prompt`` states the code-evolved disclosure (§7.1) and master §3.0-I's
  interim template-physics rule wherever the probe says a pack is evolved.

Nothing in this package prices anything: usage is measured tokens, and the
single price/constants module born at row P0-7 (master §3.0-C) owns the
per-1M entries.
"""
