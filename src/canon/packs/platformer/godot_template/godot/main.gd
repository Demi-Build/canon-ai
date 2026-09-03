# Canon platformer slice — Godot consumer (Phase 3b: registry-driven).
#
# Everything on screen resolves from the generated databases, mirroring the
# review renderer and the (throwaway) pygame harness:
#   - movement physics   <- manifest.json  (PlayerMovementSpec)
#   - combat tuning      <- manifest.json  "combat" block (combat.json):
#                           hearts, stomp damage/bounce, i-frames, spawn
#                           shield seconds — the arithmetic mirrors
#                           examples/platformer_pack/combat.py (keep in
#                           sync); combat POLICIES (checkpoint enemy
#                           reset, spawn grace) are GameRules keys
#   - tile appearance    <- TERRAIN layer (slot indices) + tilesheet regions
#   - tile physics       <- tileset slot categories + params (E.3 / 3b) —
#                           volumes carry speed_factor/gravity/impulse/
#                           damage_per_second, hazards carry damage (hearts);
#                           no hardcoded tile IDs anywhere
#   - level geometry     <- level/<stage>/<id>/collision.grid.json
#   - checkpoints        <- level.json triggers layer (respawn point moves)
#   - background         <- background layer bands
#   - enemy color/stats  <- enemy/<id>.json (size, hp, damage now LIVE);
#                           variant markers/mults from placements + the
#                           manifest's variant vocabulary
#   - decor              <- foreground layer, drawn IN FRONT of the player
# When real art replaces the placeholder tilesheet, this script does not
# change. Controls: arrows/A-D move, Space/W/Up jump or swim, R restart,
# Esc quit. Stomp: land on an enemy's head to damage it and bounce.
#
# World flow (multi-stage): a boot SPLASH card (manifest "splash" key
# art, when present) → a DK/SMW-style WORLD MAP (manifest
# "world_map", code-laid nodes clustered per biome) → START overlay
# (scene frozen, any input begins + starts the level timer) → play →
# END overlay ("Congratulations!" + time) → back to the map, node marked
# beaten and saved to user://plat_save_<seed-hash>.json. Linear unlock
# (manifest "unlock"). PLAT_LEVEL=<lid> bypasses map + overlays straight
# into gameplay — the frame-capture hook stays headless-friendly.
extends Node2D

const CELL := 32.0
const BODY_L := 0.15
const BODY_R := 0.85
# Camera framing + actor overdraw come from the manifest's GraphicsSpec
# (per-game data). Defaults match the spec model's defaults.
var view_w_cells := 20.0
var view_rows_cells := 16.0  # rows visible for a VERTICAL (climb) level
var actor_scale := 1.4
var water_alpha := 0.55  # volume tile opacity — draw only, physics untouched
# Combat tuning (manifest "combat" block; defaults mirror combat.py).
var max_hearts := 3
var stomp_damage := 6
var stomp_bounce := 0.7
var iframes_s := 1.0
var spawn_grace_s := 1.0

var manifest: Dictionary
var movement: Dictionary
var stage_id: String = ""  # current biome stage; swapped by _enter_stage
var level_stage := {}  # level_id -> stage_id (manifest "stages")
var level_display := {}  # level_id -> "1-1" display name (world_map nodes)
var level_ids: Array
var level_index := 0

# --- world flow: (SPLASH ->) MAP -> START -> PLAYING -> END -> MAP ---
enum GameState { MAP, START, PLAYING, END, SPLASH, ANIM_PREVIEW }
var game_state: int = GameState.MAP
var map_root: CanvasLayer
var overlay_root: CanvasLayer
var map_nodes: Array = []  # manifest world_map nodes (play order)
var map_selected := 0
var map_move_cool := 0.0
var input_armed := false  # any-key gates fire only after all keys release
var beaten := {}  # level_id -> true; persisted per seed
var level_time := 0.0
# Boot splash (generated world key art): hold, then fade to the map. The
# PLAT_LEVEL fast paths return from _ready before this state can exist.
const SPLASH_HOLD_S := 1.2
const SPLASH_FADE_S := 0.6
var splash_root: CanvasLayer
var splash_view: Node2D  # modulated for the fade (CanvasLayer can't be)
var splash_t := 0.0  # counts down HOLD + FADE
# ANIMATION VIEWER (PLAT_ANIM=<enemy:id|item:id|player>): every state of one
# actor playing side by side instead of a level, so a sprite can be judged in
# the surface that renders the game. Mirrors canon.packs.platformer.play's
# run_anim_preview; reuses _load_anim + _anim_index, so what it shows is what
# the game selects. Like PLAT_LEVEL it returns from _ready before the splash.
var anim_root: CanvasLayer
var anim_t := 0.0
var anim_cells: Array = []  # [{sprite, frames, durs, loop, label}]
# PLAT_ANIM_MODE=grid|sequence. grid (default) is every state side by side on
# its own clock — right for judging ONE pose. sequence walks the storyboard
# through the game's OWN ladder so `jump → fall → land` plays as the one motion
# it is. TAB toggles live. Mirrors platformer_play.run_anim_preview.
var anim_mode := "grid"
var anim_target := ""  # the PLAT_ANIM target, kept so TAB can re-enter
var anim_seq: Dictionary = {}  # sequence-mode nodes, {} in grid mode
var anim_seq_state := ""  # latched state (mirrors the game's clock latch)
var anim_seq_t := 0.0
var anim_is_enemy := false
# PLAT_SANDBOX=1 — play with NO win condition and a HUD naming the animation
# state the game picked and why. "How does it feel" is a question neither
# viewer mode can answer.
var sandbox_mode := false
var sandbox_hud: Label
var player_anim_why := ""  # the reason string from _player_anim_candidates
# The animation VOCABULARY + selection LADDER as data (animation.json at the
# pack root, written/edited by the user). Empty = this pack ships no override,
# and the hand-written ladders below are used verbatim — they are proven
# equivalent to the built-in defaults by an exhaustive parity test, so an
# override-less pack cannot diverge from pygame.
var anim_spec: Dictionary = {}
# Verification-only: PLAT_TRAJ=<path> dumps every enemy's world position +
# alerted flag per PLAYING frame (the pygame harness dumps the same format),
# so the two surfaces' movement can be diffed in world space independent of
# rendering/camera. Pairs with PLAT_LEVEL + --quit-after for a fixed run.
var _traj_file: FileAccess = null
var _traj_frame := 0
# PLAT_HOLD drives a scripted input (verification only) so run-up momentum is
# observable in a fixed capture: "right"/"left" walk, "run_right"/"run_left"
# hold RUN; jumps every PLAT_HOLD_JUMP_EVERY frames. Mirrors the pygame hook.
var _hold_mode := ""
var _hold_jump_every := 0
var _play_frame := 0
# PLAT_ACTIONS="<frame>:<down|up>,..." — scripted SINGLE-FRAME inputs the
# PLAT_HOLD vocabulary can't express: "down" holds the Down key for exactly
# that frame (pipe entry / drop-through), "up" presses jump/Up (door entry;
# dry ground only — both surfaces ignore a submerged scripted up). Frame
# numbers are the traj line numbers. Mirrors the pygame hook.
var _actions: Dictionary = {}
var save_path := ""

# --- multi-room secret levels ---------------------------------------------
# A secret room is a mini-level entered from a room_entrance trigger (pipe =
# Down, door = Up/jump) and left through its room_portal (same verb). The
# player's CARRY state (hearts/coins/held/level_time) crosses the switch;
# per-level CACHES restore what a map looked like when you left it; death
# inside a room EJECTS to the parent's checkpoint (user-locked doctrine).
# platformer_play.py mirrors every branch — mechanics parity.
var _in_room := false
var _room_return_level := ""
var _room_return_at := Vector2.ZERO
var _pending_eject := false
var _current_level_id := ""
var _level_caches: Dictionary = {}  # level_id -> persistent map state
var room_marks: Array = []  # entrance/portal marks on the CURRENT map
var _crumbled: Array = []  # cells crumbled on the CURRENT map (cache food)
var _room_id_re: RegEx = null

var grid: Array = []
var terrain: Array = []
var grid_w := 0
var grid_h := 0
var layout_axis := "horizontal"  # "vertical" climbs win at the summit ROW
var spawn := Vector2.ZERO
var exit_cell := Vector2.ZERO
var respawn_point := Vector2.ZERO
var enemy_defs := {}
# Items layer (Arc 2): world pool definitions + this level's placements.
# `coins` survives respawn (collected stays collected) and resets on a
# fresh level load. box_tile = the container tile id every consumer
# OVERLAYS onto the grid at load (collision files never carry boxes).
var item_defs := {}
var level_items: Array = []
var coins := 0
var box_tile := -1
# Held POWER-UP: one slot (empty Dictionary = none), a new pickup
# replaces it, death clears it. Timed kinds count DOWN in held_t (fuse
# arithmetic shape — parity); the shield has no timer. air_jump_ok
# re-arms on the deterministic foot probe. Mirrors platformer_play.py.
var held := {}
var held_t := 0.0
var air_jump_ok := false
# Broken item boxes stay SOLID but flip SPENT (persist across respawn,
# like crumbled floors — reset only on a level load); pops are the brief
# cosmetic rise of a box's item as it auto-collects. Mirrors pygame.
var spent_boxes := {}
var pops: Array = []
# One-shot VFX (dust/splash/sparkle prop sprites): the pops {node, t}
# idiom, faded over VFX_LIFE_S then freed. GODOT-ONLY cosmetics — the
# pygame harness stays the pre-art surface; never read by physics.
const VFX_LIFE_S := 0.35
var vfx: Array = []
# Volume-ENTRY latch (the was_airborne idiom): last frame's in-volume
# state, so a splash fires once per plunge — not every submerged frame.
var in_volume_prev := false
var enemies: Array = []
var checkpoints: Array = []

# Generated audio (late audio phase, manifest["audio"]): a looping music
# player + one player per SFX event. Missing/failed audio = silence —
# the game never depends on it.
var music_player: AudioStreamPlayer = null
var sfx_players := {}
# Music resolves by POSITION (mirrors platformer_play.py): an active user
# music_section > the level's own music_path > the stage default theme.
# ``music_cur`` is the currently-loaded track ("" = stopped) — gated so a same
# -track room switch, or staying inside a section, never restarts it.
var music_cur := ""
var stage_music_rel := ""
var level_music_path := ""
var level_music_sections: Array = []

# Physics semantics from tileset slot metadata: category sets keyed by
# tile-type int; volumes map tile-type -> params Dictionary.
var blocking := {}
var one_way := {}
var hazard := {}
var volumes := {}
# Breakable floors (sectioned-levels D) — the fuse mechanic below MUST match
# platformer_play.py byte-for-byte (parity, verified via PLAT_TRAJ).
var breakable_types := {}  # tile_type -> true (solid tiles that crumble)
var tile_fuses := {}       # Vector2i(col,row) -> seconds of hold LEFT
var tile_nodes := {}       # Vector2i(col,row) -> Sprite2D (freed on break)
var break_delay_s := 0.6
var slot_atlas := {}
var slot_category := {}
var bg_color := Color8(24, 24, 32)  # empty-slot sample = style palette
# GraphicsSpec category from the tileset manifest: "crisp" pixel art
# stays chunky when scaled to CELL; "smooth" HD art samples linearly.
var tile_filter := CanvasItem.TEXTURE_FILTER_NEAREST

var player_pos := Vector2.ZERO
var player_vy := 0.0
var player_vx := 0.0  # horizontal velocity (run-up momentum / slide)
var on_ground := false
# Coyote latch: seconds of jump-forgiveness LEFT after leaving a ledge.
# Armed each tick from the deterministic foot probe, consumed by any jump.
# Mirrors platformer_play.py byte-for-byte.
var coyote_t := 0.0
# Player animation (art track): idle/walk/jump/fall/land/skid, loaded at
# spawn. Empty → the static base sprite plays (loud fallback).
var player_anim: Dictionary = {}
var player_anim_t := 0.0
var player_anim_state := ""  # last picked state — the "once"/clock latch
var player_facing := 1.0
# Landing one-shot window (presentation only): armed on the airborne →
# grounded EDGE in the physics step, read by the anim candidates + the
# landing squash — never by physics. platformer_play.py shares the value.
const LAND_ANIM_S := 0.15
var land_t := 0.0
# Squash & stretch (godot-only visual): base node scale + untrimmed texture
# size captured at spawn for bottom-center-compensated scaling (Sprite2D
# only; the ColorRect fallback never scales).
var player_base_scale := Vector2.ONE
var player_tex_size := Vector2.ZERO
var won := false
var hearts := 3
var iframes := 0.0  # post-hit invulnerability countdown
var damage_soaked := 0.0  # fractional volume drain toward the next heart
var moved := false  # first input after (re)spawn ends the spawn grace
# Spawn SHIELD: full invincibility for a few seconds AFTER that first
# input — enemies may legitimately camp a checkpoint; this window keeps
# respawning next to one fair.
var spawn_shield := 0.0
var blink_t := 0.0  # deterministic blink clock (grace + shield + i-frames)
var hearts_root: Control = null

var world_root: Node2D
var decor_root: Node2D
var sky_root: CanvasLayer
var backdrop_root: ParallaxBackground
# FOREGROUND occlusion bands (backdrop depths > 1.0): scene-canvas
# Parallax2D nodes raised above decor_root — in front of the world and
# the player, still under the UI CanvasLayer (a CanvasLayer integer
# can't sit between 0 and 1).
var foreground_root: Node2D
var effects_root: CanvasLayer
# Lighting effect kinds build SCENE-canvas nodes (CanvasModulate /
# PointLight2D / WorldEnvironment) — the effects_root CanvasLayer would
# tint its own overlay canvas, not the world.
var light_root: Node2D
var player_light_node: PointLight2D = null
var camera: Camera2D
var player_node: CanvasItem
var status: Label

var rules := {}
var variant_defs := {}
var stage_effects: Array = []


func _ready() -> void:
	manifest = _load_json("res://manifest.json")
	movement = manifest["movement"]
	rules = manifest.get("rules", {})
	break_delay_s = float(rules.get("break_delay_s", 0.6))
	for v in manifest.get("variants", []):
		variant_defs[str(v["name"])] = v
	level_ids = manifest["levels"]
	# Multi-stage world: each level belongs to a biome stage (shared
	# tileset/backdrop/music/props); the map nodes carry the display
	# names players see ("2-1") — internal ids stay l1..lN.
	for stage_entry in manifest.get("stages", []):
		for lid in stage_entry.get("levels", []):
			level_stage[str(lid)] = str(stage_entry["stage_id"])
	for node in manifest.get("world_map", {}).get("nodes", []):
		level_display[str(node["level_id"])] = str(
			node.get("display_name", node["level_id"])
		)
	var gfx: Dictionary = manifest.get("graphics", {})
	view_w_cells = float(gfx.get("view_cells", 20))
	view_rows_cells = float(gfx.get("view_rows", 16))
	actor_scale = float(gfx.get("actor_scale", 1.4))
	water_alpha = float(gfx.get("water_alpha", 0.55))
	var combat: Dictionary = manifest.get("combat", {})
	max_hearts = int(combat.get("player_max_hearts", 3))
	stomp_damage = int(combat.get("stomp_damage", 6))
	stomp_bounce = float(combat.get("stomp_bounce_factor", 0.7))
	iframes_s = float(combat.get("hurt_iframes_s", 1.0))
	spawn_grace_s = float(combat.get("spawn_grace_s", 1.0))
	status = $UI/Status
	# Hearts HUD on the UI CanvasLayer (screen-space — a world node would
	# scale and drift under camera zoom), below the status line.
	if FileAccess.file_exists("res://animation.json"):
		var spec_v: Variant = _load_json("res://animation.json")
		if spec_v is Dictionary:
			anim_spec = spec_v
	sandbox_mode = OS.get_environment("PLAT_SANDBOX") != ""
	hearts_root = Control.new()
	hearts_root.position = Vector2(16, 44)
	$UI.add_child(hearts_root)
	if sandbox_mode:
		# SANDBOX HUD — names the animation state the game just picked and WHY.
		# Both come from _player_anim_candidates, the same ladder gameplay uses,
		# so the HUD cannot describe a decision the game didn't make. Screen
		# space, like the hearts, and on a panel because small text over a
		# parallax backdrop is unreadable. Mirrors platformer_play's HUD.
		sandbox_hud = Label.new()
		sandbox_hud.position = Vector2(16, 76)
		sandbox_hud.size = Vector2(520, 96)
		sandbox_hud.add_theme_font_size_override("font_size", 15)
		sandbox_hud.add_theme_color_override("font_color", Color(0.94, 0.925, 0.97))
		var bg := StyleBoxFlat.new()
		bg.bg_color = Color(0.07, 0.059, 0.094, 0.82)
		bg.content_margin_left = 10.0
		bg.content_margin_right = 10.0
		bg.content_margin_top = 7.0
		bg.content_margin_bottom = 7.0
		sandbox_hud.add_theme_stylebox_override("normal", bg)
		$UI.add_child(sandbox_hud)
	camera = Camera2D.new()
	add_child(camera)
	camera.make_current()
	_load_enemy_defs()
	_load_item_defs()
	map_nodes = manifest.get("world_map", {}).get("nodes", [])
	# Progress is per-WORLD user state (never part of the generated tree):
	# keyed on the manifest's content-derived world_id so a freshly generated
	# world starts from level 1 instead of inheriting a same-seed run's save.
	# Fall back to the old seed hash for manifests generated before world_id.
	var world_key := str(manifest.get("world_id", ""))
	if world_key == "":
		world_key = str(manifest.get("seed", "")).md5_text().substr(0, 12)
	save_path = "user://plat_save_%s.json" % world_key
	_load_progress()
	var traj_path := OS.get_environment("PLAT_TRAJ")
	if traj_path != "":
		_traj_file = FileAccess.open(traj_path, FileAccess.WRITE)
	_hold_mode = OS.get_environment("PLAT_HOLD")
	var hje := OS.get_environment("PLAT_HOLD_JUMP_EVERY")
	if hje != "":
		_hold_jump_every = int(hje)
	var acts := OS.get_environment("PLAT_ACTIONS")
	if acts != "":
		for token in acts.split(",", false):
			var pair := token.split(":")
			if pair.size() == 2:
				_actions[int(pair[0])] = String(pair[1]).strip_edges()
	# Animation viewer hook: PLAT_ANIM=<enemy:id|item:id|player> shows one
	# actor's states instead of playing — no map, no level, no physics.
	var env_anim := OS.get_environment("PLAT_ANIM")
	if env_anim != "":
		var env_mode := OS.get_environment("PLAT_ANIM_MODE").strip_edges()
		anim_mode = "sequence" if env_mode == "sequence" else "grid"
		_enter_anim_preview(env_anim)
		return
	# Verification/debug hook: PLAT_LEVEL=<level id> starts on that level
	# DIRECTLY (no map, no start overlay — frame-capture runs verify any
	# level without input).
	var env_level := OS.get_environment("PLAT_LEVEL")
	if env_level != "":
		var env_index := level_ids.find(env_level)
		if env_index >= 0:
			level_index = env_index
			_load_level(level_index)
			game_state = GameState.PLAYING
			return
		if _stage_for_id(env_level) != "":
			# A SECRET ROOM id (never in level_ids) boots directly by id —
			# the scenario hook for room-mechanics verification runs.
			_load_level_by_id(env_level)
			game_state = GameState.PLAYING
			return
	# Boot splash: the world's key art (manifest "splash", written by
	# WorldArtPhase). Read defensively — older trees carry no key; a
	# missing key or file boots straight to the map, exactly as before.
	var splash_rel := str(manifest.get("splash", ""))
	if splash_rel != "" and FileAccess.file_exists("res://" + splash_rel):
		_enter_splash(splash_rel)
	else:
		_enter_map()


# ---------------------------------------------------------------------------
# Boot splash — the world's key art letterboxed over black, title + "press
# any key"; hold then fade to the map (countdown-modulate idiom, no
# tweens). No asset => the map boots directly (code screens need no
# images — loud-fallback doctrine).
# ---------------------------------------------------------------------------


func _enter_splash(splash_rel: String) -> void:
	splash_root = CanvasLayer.new()
	# Above the START/END overlay layer (20): the card owns the screen.
	splash_root.layer = 30
	add_child(splash_root)
	splash_view = Node2D.new()
	splash_root.add_child(splash_view)
	var vp := get_viewport_rect().size
	var bg := ColorRect.new()
	bg.color = Color.BLACK
	bg.size = vp
	splash_view.add_child(bg)
	var image := Image.load_from_file(
		ProjectSettings.globalize_path("res://" + splash_rel)
	)
	if image != null:
		var texture := ImageTexture.create_from_image(image)
		var sprite := Sprite2D.new()
		sprite.texture = texture
		sprite.centered = false
		sprite.texture_filter = tile_filter
		# Uniform FIT scale: letterboxed, centered in the 1280x720 viewport.
		var s := minf(
			vp.x / float(image.get_width()), vp.y / float(image.get_height())
		)
		sprite.scale = Vector2(s, s)
		sprite.position = (
			vp - Vector2(image.get_width(), image.get_height()) * s
		) / 2.0
		splash_view.add_child(sprite)
	var title_label := Label.new()
	title_label.text = str(manifest["world"])
	title_label.add_theme_font_size_override("font_size", 72)
	title_label.position = Vector2(0.0, vp.y * 0.68)
	title_label.size = Vector2(vp.x, 90.0)
	title_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	splash_view.add_child(title_label)
	var sub_label := Label.new()
	sub_label.text = "press any key"
	sub_label.add_theme_font_size_override("font_size", 24)
	sub_label.position = Vector2(0.0, vp.y * 0.68 + 96.0)
	sub_label.size = Vector2(vp.x, 40.0)
	sub_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	splash_view.add_child(sub_label)
	splash_t = SPLASH_HOLD_S + SPLASH_FADE_S
	game_state = GameState.SPLASH
	input_armed = false


func _dismiss_splash() -> void:
	if splash_root != null:
		splash_root.queue_free()
		splash_root = null
		splash_view = null


func _anim_preview_sprite_rel(target: String) -> String:
	# PLAT_ANIM grammar, identical to platformer_play._anim_preview_targets:
	# enemy:<id> | item:<id> | player.
	if target == "player":
		return "sprite/player/base.png"
	var parts := target.split(":")
	if parts.size() == 2 and (parts[0] == "enemy" or parts[0] == "item"):
		return "sprite/%s/%s/base.png" % [parts[0], parts[1]]
	return ""


func _enter_anim_preview(target: String) -> void:
	# One column per animation state, each on its own clock and loop mode, over
	# the baseline the frames are bottom-anchored to. Frames come from
	# _load_anim (atlas → strips → static ladder) and are selected by
	# _anim_index, so this shows exactly what gameplay would show — the point
	# is to judge the art, not to re-implement playback.
	anim_target = target
	anim_is_enemy = target.begins_with("enemy:")
	anim_seq = {}
	anim_seq_state = ""
	anim_seq_t = 0.0
	anim_root = CanvasLayer.new()
	anim_root.layer = 30
	add_child(anim_root)
	var view := Node2D.new()
	anim_root.add_child(view)
	var vp := get_viewport_rect().size
	var bg := ColorRect.new()
	bg.color = Color(0.10, 0.086, 0.133)
	bg.size = vp
	view.add_child(bg)

	var title := Label.new()
	title.text = target
	title.add_theme_font_size_override("font_size", 28)
	title.position = Vector2(24.0, 16.0)
	title.size = Vector2(vp.x, 40.0)
	view.add_child(title)

	var sprite_rel := _anim_preview_sprite_rel(target)
	var anim := _load_anim(sprite_rel) if sprite_rel != "" else {}
	var states: Array = []
	if anim.has("states"):
		states = (anim["states"] as Dictionary).keys()
		states.sort()
	anim_cells = []
	if states.is_empty():
		var miss := Label.new()
		miss.text = (
			"no animation for %s — 🎨 generate a sprite, then 🎬 animate" % target
			if sprite_rel != "" else "unknown target %s" % target
		)
		miss.add_theme_font_size_override("font_size", 20)
		miss.position = Vector2(24.0, 96.0)
		miss.size = Vector2(vp.x, 40.0)
		view.add_child(miss)
	elif anim_mode == "sequence":
		_build_anim_sequence(view, anim, vp)
	else:
		# One cell per state, laid out across the viewport and centered.
		var cell := minf(220.0, (vp.x - 48.0) / float(states.size()))
		var box := cell - 16.0
		var top := maxf(96.0, (vp.y - box) / 2.0 - 40.0)
		var baseline := top + box
		var left := (vp.x - cell * states.size()) / 2.0
		for i in range(states.size()):
			var state := String(states[i])
			var frames: Array = (anim["states"] as Dictionary)[state]
			if frames.is_empty():
				continue
			var x := left + i * cell
			var pane := ColorRect.new()
			pane.color = Color(0.157, 0.133, 0.204)
			pane.position = Vector2(x, top)
			pane.size = Vector2(box, box)
			view.add_child(pane)
			var floor_line := ColorRect.new()
			floor_line.color = Color(0.337, 0.29, 0.408)
			floor_line.position = Vector2(x, baseline)
			floor_line.size = Vector2(box, 1.0)
			view.add_child(floor_line)
			# Zoom lives on a HOLDER, not on the Sprite2D. An atlas frame is an
			# AtlasTexture that draws its trimmed region at a per-frame margin
			# offset; scaling the sprite itself leaves that offset unscaled, so
			# frames drift off the baseline (death sat ~70px high). A parent
			# transform scales the offset and the region together. Scale comes
			# from the NOMINAL square (CELL) — never a texture's reported size,
			# which varies per frame — matching gameplay, where _actor_visual
			# sets the scale ONCE from base.png and then swaps textures.
			# Integer scale: pixel art shimmers at fractional zoom.
			var s := maxf(1.0, floorf(box / CELL))
			var holder := Node2D.new()
			holder.scale = Vector2(s, s)
			holder.position = Vector2(
				x + (box - CELL * s) / 2.0, baseline - CELL * s
			)
			view.add_child(holder)
			var sprite := Sprite2D.new()
			sprite.centered = false
			sprite.texture_filter = tile_filter
			sprite.texture = frames[0]
			holder.add_child(sprite)
			var name_label := Label.new()
			name_label.text = state
			name_label.add_theme_font_size_override("font_size", 16)
			name_label.position = Vector2(x, baseline + 8.0)
			name_label.size = Vector2(cell, 22.0)
			view.add_child(name_label)
			var meta_label := Label.new()
			meta_label.add_theme_font_size_override("font_size", 13)
			meta_label.position = Vector2(x, baseline + 30.0)
			meta_label.size = Vector2(cell, 20.0)
			view.add_child(meta_label)
			anim_cells.append({
				"sprite": sprite,
				"frames": frames,
				"durs": (anim["durs"] as Dictionary).get(state, [0.12]),
				"loop": String((anim["loops"] as Dictionary).get(state, "loop")),
				"label": meta_label,
				"state": state,
			})

	var help := Label.new()
	help.text = "TAB grid/sequence   SPACE replay   ESC quit"
	help.add_theme_font_size_override("font_size", 14)
	help.position = Vector2(24.0, vp.y - 40.0)
	help.size = Vector2(vp.x, 24.0)
	view.add_child(help)
	var mode_lbl := Label.new()
	mode_lbl.text = anim_mode
	mode_lbl.add_theme_font_size_override("font_size", 14)
	mode_lbl.position = Vector2(vp.x - 220.0, 22.0)
	mode_lbl.size = Vector2(200.0, 20.0)
	mode_lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	view.add_child(mode_lbl)
	anim_t = 0.0
	game_state = GameState.ANIM_PREVIEW
	input_armed = false


func _build_anim_sequence(view: Node2D, anim: Dictionary, vp: Vector2) -> void:
	# ONE large actor playing the chain the game's ladder picks, over a timeline
	# of the storyboard. Mirrors platformer_play._draw_anim_sequence: it draws,
	# it decides nothing — the state comes from _sequence_at + _anim_pick, the
	# same machinery gameplay uses, so it cannot drift from what the game shows.
	var box := minf(360.0, vp.y - 300.0)
	var top := 96.0
	var baseline := top + box
	var left := 48.0
	var pane := ColorRect.new()
	pane.color = Color(0.157, 0.133, 0.204)
	pane.position = Vector2(left, top)
	pane.size = Vector2(box, box)
	view.add_child(pane)
	var floor_line := ColorRect.new()
	floor_line.color = Color(0.337, 0.29, 0.408)
	floor_line.position = Vector2(left, baseline)
	floor_line.size = Vector2(box, 1.0)
	view.add_child(floor_line)
	# Zoom on a HOLDER, never the Sprite2D — an atlas frame is an AtlasTexture
	# drawing its trimmed region at a per-frame margin offset, and scaling the
	# sprite leaves that offset unscaled (the phase-1 bug). Same trick as grid.
	var s := maxf(1.0, floorf(box / CELL))
	var holder := Node2D.new()
	holder.scale = Vector2(s, s)
	holder.position = Vector2(left + (box - CELL * s) / 2.0, baseline - CELL * s)
	view.add_child(holder)
	var sprite := Sprite2D.new()
	sprite.centered = false
	sprite.texture_filter = tile_filter
	holder.add_child(sprite)

	var y := baseline + 16.0
	var state_lbl := Label.new()
	state_lbl.add_theme_font_size_override("font_size", 26)
	state_lbl.position = Vector2(left, y)
	state_lbl.size = Vector2(vp.x - left, 32.0)
	view.add_child(state_lbl)
	var why_lbl := Label.new()
	why_lbl.add_theme_font_size_override("font_size", 15)
	why_lbl.position = Vector2(left, y + 34.0)
	why_lbl.size = Vector2(vp.x - left, 22.0)
	view.add_child(why_lbl)
	var ladder_lbl := Label.new()
	ladder_lbl.add_theme_font_size_override("font_size", 14)
	ladder_lbl.position = Vector2(left, y + 58.0)
	ladder_lbl.size = Vector2(vp.x - left, 22.0)
	view.add_child(ladder_lbl)

	# The storyboard as a bar: each segment proportional to its duration, the
	# current one lit, so the chain reads as one motion with a playhead.
	var segs: Array = ENEMY_SEQUENCE if anim_is_enemy else ANIM_SEQUENCE
	var total := 0.0
	for seg in segs:
		total += float(seg[0])
	var bar_y := y + 92.0
	var bar_w := vp.x - left * 2.0
	var rects: Array = []
	var bx := left
	for seg in segs:
		var w := bar_w * float(seg[0]) / total
		var r := ColorRect.new()
		r.color = Color(0.19, 0.165, 0.243)
		r.position = Vector2(bx, bar_y)
		r.size = Vector2(maxf(2.0, w - 3.0), 10.0)
		view.add_child(r)
		rects.append(r)
		bx += w
	var playhead := ColorRect.new()
	playhead.color = Color(0.925, 0.906, 0.961)
	playhead.size = Vector2(2.0, 16.0)
	playhead.position = Vector2(left, bar_y - 3.0)
	view.add_child(playhead)
	anim_seq = {
		"sprite": sprite,
		"anim": anim,
		"state": state_lbl,
		"why": why_lbl,
		"ladder": ladder_lbl,
		"rects": rects,
		"playhead": playhead,
		"bar_left": left,
		"bar_w": bar_w,
		"total": total,
	}


func _anim_preview_process(delta: float) -> void:
	# SPACE rewinds every clock so the `once` states (jump/land/hurt/death),
	# which hold their last frame by design, can be replayed.
	if input_armed and Input.is_key_pressed(KEY_SPACE):
		anim_t = 0.0
		input_armed = false
	# TAB toggles grid ↔ sequence — a toggle, not a replacement: the grid is
	# still the right tool for judging a single pose. Rebuild in place.
	if input_armed and Input.is_key_pressed(KEY_TAB):
		input_armed = false
		anim_mode = "grid" if anim_mode == "sequence" else "sequence"
		if anim_root != null:
			anim_root.queue_free()
			anim_root = null
		anim_cells = []
		_enter_anim_preview(anim_target)
		return
	anim_t += delta
	if not anim_seq.is_empty():
		_anim_sequence_process(delta)
		return
	for cell in anim_cells:
		var frames: Array = cell["frames"]
		var i := _anim_index(anim_t, cell["durs"], cell["loop"])
		i = clampi(i, 0, frames.size() - 1)
		(cell["sprite"] as Sprite2D).texture = frames[i]
		(cell["label"] as Label).text = "%d/%d %s" % [
			i + 1, frames.size(), cell["loop"]
		]


func _anim_sequence_process(delta: float) -> void:
	# Drive the storyboard through the game's OWN ladder. The viewer never
	# names a state itself — _sequence_at builds candidates, _anim_pick chooses,
	# and the state-change latch mirrors gameplay so a "once" state such as
	# `land` restarts from frame 0 every pass through the chain.
	anim_seq_t += delta
	var anim: Dictionary = anim_seq["anim"]
	var states: Dictionary = anim["states"]
	var res := _sequence_at(anim_t, anim_is_enemy)
	var cand: Array = res[0]
	var state := _anim_pick(cand, states)
	if state != anim_seq_state:
		anim_seq_state = state
		anim_seq_t = 0.0
	var frames: Array = states[state]
	var i := clampi(
		_anim_index(anim_seq_t, anim["durs"][state], str(anim["loops"][state])),
		0,
		frames.size() - 1,
	)
	(anim_seq["sprite"] as Sprite2D).texture = frames[i]
	(anim_seq["state"] as Label).text = "%s   %d/%d %s" % [
		state, i + 1, frames.size(), str(anim["loops"][state])
	]
	(anim_seq["why"] as Label).text = String(res[1])
	# The ladder that produced the pick, winner marked. A pick below the head
	# means that state has no art and the game fell through to this one.
	# The winner is the FIRST candidate that exists, so mark by INDEX — a state
	# like `walk` appears twice in the ladder and only one occurrence won.
	var parts: PackedStringArray = []
	var win_i := cand.find(state)
	for j in range(cand.size()):
		parts.append(("[%s]" % cand[j]) if j == win_i else String(cand[j]))
	(anim_seq["ladder"] as Label).text = " > ".join(parts)
	var rects: Array = anim_seq["rects"]
	var seg_i := int(res[2])
	for j in range(rects.size()):
		(rects[j] as ColorRect).color = (
			Color(0.424, 0.376, 0.518) if j == seg_i else Color(0.19, 0.165, 0.243)
		)
	var total := float(anim_seq["total"])
	var u := fmod(anim_t, total) / total if total > 0.0 else 0.0
	(anim_seq["playhead"] as ColorRect).position.x = (
		float(anim_seq["bar_left"]) + float(anim_seq["bar_w"]) * u
	)


# ---------------------------------------------------------------------------
# World map (DK/SMW style) — drawn from manifest.world_map; positions are
# normalized 0..1 (deterministic code layout, biome clusters left→right).
# ---------------------------------------------------------------------------


func _first_unbeaten() -> int:
	for i in range(level_ids.size()):
		if not beaten.has(str(level_ids[i])):
			return i
	return level_ids.size() - 1  # all beaten: everything stays selectable


func _world_complete() -> bool:
	for lid in level_ids:
		if not beaten.has(str(lid)):
			return false
	return true


func _enter_map() -> void:
	# The map replaces the level view: free the world, keep the last
	# stage's music playing (its biome is where the player stands).
	for node in [world_root, backdrop_root, foreground_root, effects_root, light_root]:
		if node != null:
			node.queue_free()
	world_root = null
	backdrop_root = null
	foreground_root = null
	effects_root = null
	light_root = null
	player_light_node = null
	_dismiss_overlay()
	_dismiss_splash()
	if hearts_root != null:
		hearts_root.visible = false
	map_selected = clampi(_first_unbeaten(), 0, level_ids.size() - 1)
	_build_map()
	game_state = GameState.MAP
	input_armed = false
	status.text = "%s — world map  (arrows select, Enter plays, Esc quits)" % (
		manifest["world"]
	)


func _map_dot(center: Vector2, radius: float, color: Color) -> Polygon2D:
	var dot := Polygon2D.new()
	var points := PackedVector2Array()
	for i in range(14):
		var a := TAU * float(i) / 14.0
		points.append(center + Vector2(cos(a), sin(a)) * radius)
	dot.polygon = points
	dot.color = color
	return dot


func _map_ring(center: Vector2, radius: float, color: Color) -> Line2D:
	var ring := Line2D.new()
	for i in range(15):
		var a := TAU * float(i) / 14.0
		ring.add_point(center + Vector2(cos(a), sin(a)) * radius)
	ring.width = 3.0
	ring.default_color = color
	return ring


func _build_map() -> void:
	if map_root != null:
		map_root.queue_free()
	map_root = CanvasLayer.new()
	# Below the UI layer: the status line stays readable over the map.
	map_root.layer = 0
	add_child(map_root)
	var vp := get_viewport_rect().size
	var bg := ColorRect.new()
	bg.color = Color(0.055, 0.055, 0.085)
	bg.size = vp
	map_root.add_child(bg)
	var margin := Vector2(vp.x * 0.08, vp.y * 0.2)
	var span := vp - margin * 2.0
	var pts := {}
	for node in map_nodes:
		pts[str(node["level_id"])] = margin + Vector2(
			float(node["pos"][0]) * span.x, float(node["pos"][1]) * span.y
		)
	# Biome regions, tinted with each stage's own palette background.
	var palettes: Dictionary = manifest.get("palettes", {})
	for stage_entry in manifest.get("stages", []):
		var sid := str(stage_entry["stage_id"])
		var lids: Array = stage_entry.get("levels", [])
		if lids.is_empty():
			continue
		var min_x := INF
		var max_x := -INF
		for lid in lids:
			min_x = minf(min_x, pts[str(lid)].x)
			max_x = maxf(max_x, pts[str(lid)].x)
		var tint := Color(str(
			palettes.get(sid, {}).get("background", "#20222c")
		))
		var region := ColorRect.new()
		region.color = Color(tint.r, tint.g, tint.b, 0.55)
		region.position = Vector2(min_x - 44.0, margin.y * 0.55)
		region.size = Vector2(max_x - min_x + 88.0, vp.y - margin.y * 1.1)
		map_root.add_child(region)
		var biome_label := Label.new()
		biome_label.text = "%s — %s" % [
			str(stage_entry.get("biome", "")),
			str(stage_entry.get("theme", "")),
		]
		biome_label.position = Vector2(min_x - 34.0, margin.y * 0.55 + 8.0)
		map_root.add_child(biome_label)
	# The path, then its level nodes on top.
	var path := Line2D.new()
	path.width = 4.0
	path.default_color = Color(0.9, 0.88, 0.8, 0.45)
	for lid in level_ids:
		path.add_point(pts[str(lid)])
	map_root.add_child(path)
	var unlocked_max := _first_unbeaten()
	for i in range(level_ids.size()):
		var lid := str(level_ids[i])
		var p: Vector2 = pts[lid]
		var color := Color(0.42, 0.42, 0.5)  # locked
		if beaten.has(lid):
			color = Color(1.0, 0.82, 0.29)  # beaten: gold
		elif i <= unlocked_max:
			color = Color.WHITE  # unlocked, unbeaten
		map_root.add_child(_map_dot(p, 12.0, color))
		var name_label := Label.new()
		name_label.text = str(level_display.get(lid, lid))
		name_label.position = p + Vector2(-13.0, 17.0)
		map_root.add_child(name_label)
	map_root.add_child(_map_ring(
		pts[str(level_ids[map_selected])], 19.0, Color(0.25, 1.0, 0.44)
	))
	if _world_complete():
		var banner := Label.new()
		banner.text = "WORLD COMPLETE!"
		banner.add_theme_font_size_override("font_size", 56)
		banner.position = Vector2(vp.x / 2.0 - 260.0, vp.y * 0.06)
		map_root.add_child(banner)


func _map_process(delta: float) -> void:
	map_move_cool = maxf(0.0, map_move_cool - delta)
	var step := 0
	if Input.is_key_pressed(KEY_RIGHT) or Input.is_key_pressed(KEY_D):
		step = 1
	elif Input.is_key_pressed(KEY_LEFT) or Input.is_key_pressed(KEY_A):
		step = -1
	if step != 0 and map_move_cool <= 0.0:
		map_selected = clampi(
			map_selected + step, 0,
			clampi(_first_unbeaten(), 0, level_ids.size() - 1)
		)
		map_move_cool = 0.18
		_build_map()
	if input_armed and (
		Input.is_key_pressed(KEY_ENTER)
		or Input.is_key_pressed(KEY_SPACE)
		or Input.is_key_pressed(KEY_Z)
	):
		if map_root != null:
			map_root.queue_free()
			map_root = null
		level_index = map_selected
		_load_level(level_index)
		_show_overlay("START", str(level_display.get(
			str(level_ids[level_index]), level_ids[level_index]
		)))
		game_state = GameState.START
		input_armed = false


# ---------------------------------------------------------------------------
# Start/end overlays — the scene stands frozen behind a translucent veil;
# any input moves the flow forward (playtest ask: every level has an
# explicit start, and a "Congratulations!" close with the level time).
# ---------------------------------------------------------------------------


func _show_overlay(title: String, subtitle: String) -> void:
	_dismiss_overlay()
	overlay_root = CanvasLayer.new()
	overlay_root.layer = 20
	add_child(overlay_root)
	var vp := get_viewport_rect().size
	var veil := ColorRect.new()
	veil.color = Color(0.0, 0.0, 0.0, 0.55)
	veil.size = vp
	overlay_root.add_child(veil)
	var title_label := Label.new()
	title_label.text = title
	title_label.add_theme_font_size_override("font_size", 72)
	title_label.position = Vector2(vp.x / 2.0 - 220.0, vp.y * 0.36)
	title_label.size = Vector2(440.0, 90.0)
	title_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	overlay_root.add_child(title_label)
	var sub_label := Label.new()
	sub_label.text = subtitle
	sub_label.add_theme_font_size_override("font_size", 28)
	sub_label.position = Vector2(vp.x / 2.0 - 220.0, vp.y * 0.36 + 96.0)
	sub_label.size = Vector2(440.0, 40.0)
	sub_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	overlay_root.add_child(sub_label)


func _dismiss_overlay() -> void:
	if overlay_root != null:
		overlay_root.queue_free()
		overlay_root = null


func _format_time(seconds: float) -> String:
	return "%d:%04.1f" % [int(seconds) / 60, fmod(seconds, 60.0)]


func _load_progress() -> void:
	var file := FileAccess.open(save_path, FileAccess.READ)
	if file == null:
		return
	var data: Variant = JSON.parse_string(file.get_as_text())
	if data is Dictionary:
		for lid in data.get("beaten", []):
			beaten[str(lid)] = true


func _save_progress() -> void:
	var file := FileAccess.open(save_path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify({"beaten": beaten.keys()}))


func _enter_stage(new_stage: String) -> void:
	# Stage assets are shared by the biome's levels — swap tileset, stage
	# effects, and audio only when the level's stage actually changes.
	if new_stage == stage_id:
		return
	stage_id = new_stage
	slot_atlas.clear()
	slot_category.clear()
	blocking.clear()
	one_way.clear()
	hazard.clear()
	volumes.clear()
	_load_tileset()
	var stage: Dictionary = _load_json("res://stage/%s/stage.json" % stage_id)
	stage_effects = stage.get("effects", [])
	_setup_audio(manifest.get("audio", {}).get(stage_id, {}))


func _load_json(path: String) -> Variant:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		push_error("canon: missing %s — run the slice generator first." % path)
		return {}
	return JSON.parse_string(file.get_as_text())


func _load_tileset() -> void:
	# Appearance AND physics resolve through the Tileset artifact.
	var ts: Dictionary = _load_json("res://tileset/%s/manifest.json" % stage_id)
	if str(ts.get("render_filter", "crisp")) == "smooth":
		tile_filter = CanvasItem.TEXTURE_FILTER_LINEAR
	var image := Image.load_from_file(
		ProjectSettings.globalize_path("res://" + str(ts["tilesheet_path"]))
	)
	var sheet := ImageTexture.create_from_image(image)
	for slot in ts["slots"]:
		var region: Array = slot["px_region"]
		var atlas := AtlasTexture.new()
		atlas.atlas = sheet
		atlas.region = Rect2(region[0], region[1], region[2], region[3])
		var index := int(slot["index"])
		var tile_type := int(slot["tile_type"])
		slot_atlas[index] = atlas
		slot_category[index] = str(slot.get("collision", ""))
		if str(slot.get("collision", "")) == "empty":
			# Region AVERAGE, not one pixel — generated sky textures
			# resolve to their palette-conformed mean for horizon bands.
			var empty_region := image.get_region(
				Rect2i(region[0], region[1], region[2], region[3])
			)
			empty_region.resize(1, 1, Image.INTERPOLATE_BILINEAR)
			bg_color = empty_region.get_pixel(0, 0)
		match str(slot.get("collision", "")):
			"solid":
				blocking[tile_type] = true
				if bool(slot.get("params", {}).get("breakable", false)):
					breakable_types[tile_type] = true
				if bool(slot.get("params", {}).get("container", false)):
					box_tile = tile_type  # item BOX (items-layer overlay)
			"one_way":
				one_way[tile_type] = true
			"hazard":
				# Params carry "damage" in HEARTS (default 1 in _hurt).
				hazard[tile_type] = slot.get("params", {})
			"volume":
				volumes[tile_type] = slot.get("params", {})


func _load_enemy_defs() -> void:
	for enemy_id in manifest["enemies"]:
		enemy_defs[enemy_id] = _load_json("res://enemy/%s.json" % enemy_id)


func _load_item_defs() -> void:
	for item_id in manifest.get("items", []):
		item_defs[item_id] = _load_json("res://item/%s.json" % item_id)


func _stage_for_id(level_id: String) -> String:
	# The stage owning a level id. A SECRET ROOM id (l3r1 — never in the
	# manifest stages lists) resolves to its parent's stage, so a room is
	# loadable even as the very first level (PLAT_LEVEL=l3r1 scenarios).
	if level_stage.has(level_id):
		return str(level_stage[level_id])
	if _room_id_re == null:
		_room_id_re = RegEx.new()
		_room_id_re.compile("^(.+\\d)r\\d+$")
	var m := _room_id_re.search(level_id)
	if m != null and level_stage.has(m.get_string(1)):
		return str(level_stage[m.get_string(1)])
	# DRAFT levels (`canon level create`, and the sandbox room) live on disk but
	# are deliberately kept OUT of the manifest until `level publish`, so the
	# lookups above cannot see them. Fall back to disk — the same fix
	# platformer_play._stage_for and export_level_bundle both needed.
	for st in manifest.get("stages", []):
		var sid := str((st as Dictionary).get("stage_id", ""))
		if sid != "" and FileAccess.file_exists(
			"res://level/%s/%s/level.json" % [sid, level_id]
		):
			return sid
	return stage_id


func _load_level(index: int) -> void:
	# Index wrapper for the world-map/retry flow; ids are the real key —
	# a SECRET ROOM (multi-room arc) is a level id that is never in
	# level_ids, loaded by id through _load_level_by_id.
	level_index = index
	_load_level_by_id(level_ids[index])


func _load_level_by_id(level_id: String, preserve := false, arrive: Variant = null) -> void:
	# ``preserve`` = a state-preserving ROOM SWITCH: the carried player
	# state (hearts/coins/held/timer) survives, and the map's persistent
	# cache restores after the rebuild. ``arrive`` overrides the landing
	# cell (a return point, or the sentinel "respawn" for an eject).
	_current_level_id = level_id
	_enter_stage(_stage_for_id(level_id))
	var base := "res://level/%s/%s" % [stage_id, level_id]
	var level: Dictionary = _load_json(base + "/level.json")
	# Per-level overrides (combat/level-picks arc): re-derive the
	# EFFECTIVE rules/movement for THIS level — manifest base + the
	# level's validated override dicts. Every other rules read is live
	# off the member; break_delay_s is the one cached scalar. Mirrors
	# platformer_play.py's run_level merge (mechanics parity).
	movement = (manifest["movement"] as Dictionary).duplicate()
	for k in (level.get("movement_overrides", {}) as Dictionary):
		movement[k] = level["movement_overrides"][k]
	rules = (manifest.get("rules", {}) as Dictionary).duplicate()
	for k in (level.get("rules_overrides", {}) as Dictionary):
		rules[k] = level["rules_overrides"][k]
	break_delay_s = float(rules.get("break_delay_s", 0.6))
	# Per-level music override (additive) — the resolver falls back to the
	# stage theme when these are empty. Read here; applied after spawn/axis.
	level_music_path = str(level.get("music_path", ""))
	level_music_sections = level.get("music_sections", [])
	grid = _load_json(base + "/collision.grid.json")["collision"]
	terrain = _load_json(base + "/terrain.grid.json")["terrain"]
	tile_fuses.clear()  # per-level breakable state (broken tiles reset on reload)
	var background: Array = _load_json(base + "/background.grid.json")["background"]
	grid_h = grid.size()
	grid_w = grid[0].size() if grid_h > 0 else 0
	spawn = Vector2(level["spawn"][0], level["spawn"][1])
	exit_cell = Vector2(level["exit"][0], level["exit"][1])
	respawn_point = spawn
	# Framing is CAMERA ZOOM at a stable window, never a window resize.
	# HORIZONTAL levels frame by WIDTH (view_cells columns span the viewport,
	# a deliberate intimate/vista exception overriding it); VERTICAL (climb)
	# levels frame by HEIGHT (view_rows rows span the viewport — a tall shaft
	# you scroll up). Either way the zoom is clamped so the view can't show
	# past the level bounds.
	var vp := get_viewport_rect().size
	layout_axis = str(level.get("layout_axis", "horizontal"))
	# Seed music for this level from the spawn cell (stage default unless the
	# level/room carries its own track). Per-frame section swaps happen in
	# _process; a same-track room switch is gated out by music_cur.
	_apply_music(_music_cell(spawn))
	var k: float
	if layout_axis == "vertical":
		var vr_override: Variant = level.get("view_rows")
		var eff_view_rows: float = float(vr_override) if vr_override != null else view_rows_cells
		eff_view_rows = minf(float(grid_h), eff_view_rows)
		k = vp.y / (eff_view_rows * CELL)
	else:
		var view_override: Variant = level.get("view_cells")
		var eff_view_cells: float = float(view_override) if view_override != null else view_w_cells
		eff_view_cells = minf(float(grid_w), eff_view_cells)
		k = vp.x / (eff_view_cells * CELL)
	k = maxf(k, vp.x / (grid_w * CELL))
	k = maxf(k, vp.y / (grid_h * CELL))
	camera.zoom = Vector2(k, k)
	var view_w := vp.x / k  # visible world width, px (sky/effects sizing)
	camera.limit_left = 0
	camera.limit_right = int(grid_w * CELL)
	camera.limit_top = 0
	camera.limit_bottom = int(grid_h * CELL)
	camera.position = Vector2(view_w / 2.0, grid_h * CELL / 2.0)

	# Items layer BEFORE tile building: box placements overlay their solid
	# tile onto the grid, so _build_tiles gives them real tile nodes and
	# collision Just Works (platformer_play.py overlays identically).
	# Fresh level load = fresh item state; respawn never touches it. A
	# room SWITCH preserves the carried coins (per-map state comes back
	# from the cache after the rebuild instead).
	level_items.clear()
	if not preserve:
		coins = 0
	spent_boxes.clear()
	pops.clear()
	vfx.clear()  # nodes die with world_root; the pool list resets with it
	in_volume_prev = false
	_crumbled = []
	if FileAccess.file_exists(base + "/items.json"):
		for rec in (_load_json(base + "/items.json") as Array):
			var d: Dictionary = item_defs.get(str(rec["item_id"]), {})
			var stats: Dictionary = d.get("stats", {})
			var it := {
				"x": int(rec["x"]),
				"y": int(rec["y"]),
				"source": str(rec.get("source", "trail")),
				"kind": str(d.get("kind", "coin")),
				"params": d.get("params", {}),
				"color": str(stats.get("placeholder_color", "#ffd700")),
				"sprite_path": str(d.get("sprite_path", "")),
				"collected": false,
				"node": null,
			}
			level_items.append(it)
			if it["source"] == "box" and box_tile >= 0:
				grid[it["y"]][it["x"]] = box_tile

	if world_root != null:
		world_root.queue_free()
	world_root = Node2D.new()
	add_child(world_root)
	_build_background(background, view_w)
	_build_backdrop()
	_build_tiles()
	_build_markers(level.get("triggers", []))
	_build_items()
	_spawn_enemies(_load_json(base + "/entities.json"))
	_spawn_player(preserve)
	# Persistent per-map state comes back AFTER every node exists
	# (collected items hide, spent boxes shade, crumbles clear, claimed
	# checkpoints raise, dead enemies stay dead, respawn point moves).
	_restore_level_cache()
	if preserve:
		if typeof(arrive) == TYPE_STRING and str(arrive) == "respawn":
			_place_player_at(respawn_point)
		elif arrive != null:
			_place_player_at(arrive)
		_refresh_hearts()
	_build_decor(level.get("foreground", []))
	if foreground_root != null:
		# Occlusion bands in front of everything on the scene canvas —
		# decor and the player included. CanvasLayers (UI/effects/sky)
		# order by layer number, not sibling order, so this only reorders
		# the layer-0 draw.
		move_child(foreground_root, get_child_count() - 1)
	_build_effects()
	won = false
	if hearts_root != null:
		hearts_root.visible = true  # hidden while the world map is up
	status.text = "%s — %s  (R restart, Esc quit)" % [
		manifest["world"], level_display.get(level_id, level_id),
	]


func _build_background(background: Array, view_w: float) -> void:
	# One rect per horizon band — the game's background color (style
	# palette via the empty tileset slot), lightened toward the top.
	# The bands are WORLD-anchored (row coordinates), so the layer must
	# follow the camera — framing is zoom now, and a static layer would
	# misalign the horizon against the terrain. Parallax bands (layer
	# -100) draw on top of it.
	if sky_root != null:
		sky_root.queue_free()
	sky_root = CanvasLayer.new()
	sky_root.layer = -101
	sky_root.follow_viewport_enabled = true
	add_child(sky_root)
	var band_start := 0
	for y in range(1, background.size() + 1):
		var ended := y == background.size()
		if ended or int(background[y][0]) != int(background[band_start][0]):
			var band := int(background[band_start][0])
			var scale := 1.0 + 0.16 * float(2 - band)
			var rect := ColorRect.new()
			rect.color = Color(
				minf(bg_color.r * scale, 1.0),
				minf(bg_color.g * scale, 1.0),
				minf(bg_color.b * scale, 1.0),
			)
			rect.position = Vector2(0, band_start * CELL)
			# Full grid width, not the camera view — the sky must cover
			# any viewport (movie mode / resized windows showed gray).
			rect.size = Vector2(
				maxf(view_w, grid_w * CELL), (y - band_start) * CELL
			)
			sky_root.add_child(rect)
			band_start = y


func _build_backdrop() -> void:
	# Generated parallax scenery (art track) — ParallaxBackground scrolls
	# each band by its authored depth as the camera moves. Absent bands
	# (fake/fallback path) leave the gradient sky as the whole backdrop.
	# Depth SPLIT: bands <= 1.0 land in the layer -100 ParallaxBackground
	# behind gameplay; depth > 1.0 = FOREGROUND occluders on the scene
	# canvas (Parallax2D, scroll_scale > 1), raised above decor_root after
	# _build_decor so they draw in front of the world and the player but
	# behind the UI CanvasLayer.
	if backdrop_root != null:
		backdrop_root.queue_free()
		backdrop_root = null
	if foreground_root != null:
		foreground_root.queue_free()
		foreground_root = null
	if not FileAccess.file_exists("res://backdrop/%s/manifest.json" % stage_id):
		return
	var bd: Dictionary = _load_json("res://backdrop/%s/manifest.json" % stage_id)
	var paths: Array = bd.get("band_paths", [])
	var depths: Array = bd.get("depths", [])
	if paths.is_empty():
		return
	backdrop_root = ParallaxBackground.new()
	backdrop_root.layer = -100
	add_child(backdrop_root)
	for i in range(paths.size()):
		var image := Image.load_from_file(
			ProjectSettings.globalize_path("res://" + str(paths[i]))
		)
		if image == null:
			continue
		var depth := float(depths[i]) if i < depths.size() else 0.5
		var texture := ImageTexture.create_from_image(image)
		var s := (grid_h * CELL) / float(image.get_height())
		var band_w := image.get_width() * s
		if depth > 1.0:
			if foreground_root == null:
				foreground_root = Node2D.new()
				add_child(foreground_root)
			var par := Parallax2D.new()
			par.scroll_scale = Vector2(depth, 1.0)
			par.repeat_size = Vector2(band_w, 0)
			_add_band_copies(par, texture, s, band_w)
			foreground_root.add_child(par)
			continue
		var layer := ParallaxLayer.new()
		layer.motion_scale = Vector2(depth, 1.0)
		layer.motion_mirroring = Vector2(band_w, 0)
		_add_band_copies(layer, texture, s, band_w)
		backdrop_root.add_child(layer)


func _add_band_copies(parent: Node2D, texture: Texture2D, s: float, band_w: float) -> void:
	# Two copies side by side: mirroring/repeat alone left a void beyond
	# one band width until the camera moved (seen in frame captures).
	for copy_index in range(2):
		var sprite := Sprite2D.new()
		sprite.texture = texture
		sprite.centered = false
		sprite.texture_filter = tile_filter
		sprite.scale = Vector2(s, s)
		sprite.position = Vector2(band_w * copy_index, 0)
		parent.add_child(sprite)


func _build_effects() -> void:
	# Stage-effect vocabulary: values from stage.json, KINDS interpreted
	# here. Unknown names are inert until an interpreter exists (E.7).
	# Screen-space ambience overlay (CanvasLayer, camera-independent) —
	# sized to the viewport so particles always fall across the visible
	# frame regardless of camera zoom or scroll.
	var vp := get_viewport_rect().size
	if effects_root != null:
		effects_root.queue_free()
		effects_root = null
	if light_root != null:
		light_root.queue_free()
		light_root = null
	player_light_node = null
	if stage_effects.is_empty():
		return
	effects_root = CanvasLayer.new()
	effects_root.layer = 10
	add_child(effects_root)
	# Lighting kinds live on the SCENE canvas: a CanvasModulate inside
	# effects_root (its own CanvasLayer) would tint the overlay, not the
	# world. Sibling order is irrelevant for modulate/light/environment.
	light_root = Node2D.new()
	add_child(light_root)
	for effect in stage_effects:
		var params: Dictionary = effect.get("params", {})
		match str(effect.get("name", "")):
			"particles_falling":
				var speed := float(params.get("speed", 80))
				var particles := CPUParticles2D.new()
				particles.amount = int(params.get("density", 40))
				particles.lifetime = (vp.y + 64.0) / maxf(speed, 1.0)
				particles.preprocess = particles.lifetime
				particles.emission_shape = CPUParticles2D.EMISSION_SHAPE_RECTANGLE
				particles.emission_rect_extents = Vector2(vp.x / 2.0 + 32.0, 4.0)
				particles.position = Vector2(vp.x / 2.0, -8.0)
				particles.direction = Vector2(float(params.get("drift", 0)), speed)
				particles.spread = 6.0
				particles.gravity = Vector2.ZERO
				particles.initial_velocity_min = speed * 0.8
				particles.initial_velocity_max = speed * 1.2
				particles.scale_amount_min = float(params.get("size", 2))
				particles.scale_amount_max = float(params.get("size", 2)) * 1.5
				particles.color = Color(str(params.get("color", "#e8e8f0")))
				effects_root.add_child(particles)
			"canvas_tint":
				_build_canvas_tint(params)
			"player_light":
				_build_player_light(params)
			"glow":
				_build_glow(params)
			_:
				pass  # unknown kinds stay inert until an interpreter exists (E.7)


func _build_canvas_tint(params: Dictionary) -> void:
	# Ambient color grade: blend the scene canvas toward the effect color
	# by strength. The sky and the parallax bands live on their OWN
	# CanvasLayers (CanvasModulate is per-canvas), so they take the same
	# blend via modulate — layers must not visibly disagree.
	var strength := clampf(float(params.get("strength", 0.35)), 0.0, 1.0)
	var blend := Color.WHITE.lerp(
		Color(str(params.get("color", "#e8e8f0"))), strength
	)
	var tint := CanvasModulate.new()
	tint.color = blend
	light_root.add_child(tint)
	if sky_root != null:
		for child in sky_root.get_children():
			(child as CanvasItem).modulate = blend
	if backdrop_root != null:
		for layer in backdrop_root.get_children():
			(layer as CanvasItem).modulate = blend


func _build_player_light(params: Dictionary) -> void:
	# A radial light glued to the player (positioned per-frame beside the
	# node update — visual only, physics never reads it) over a canvas
	# darkened by `dim` toward black. Texture is a code-built radial
	# gradient — the template ships no imported assets.
	var dark := CanvasModulate.new()
	dark.color = Color.WHITE.lerp(
		Color.BLACK, clampf(float(params.get("dim", 0.6)), 0.0, 1.0)
	)
	light_root.add_child(dark)
	var gradient := Gradient.new()
	gradient.set_color(0, Color(str(params.get("color", "#e8e8f0"))))
	gradient.set_color(1, Color(0.0, 0.0, 0.0, 0.0))
	var texture := GradientTexture2D.new()
	texture.gradient = gradient
	texture.fill = GradientTexture2D.FILL_RADIAL
	texture.fill_from = Vector2(0.5, 0.5)
	texture.fill_to = Vector2(0.5, 0.0)
	var light := PointLight2D.new()
	light.texture = texture
	# GradientTexture2D is 64px square: scale so the lit diameter spans
	# 2 x radius (params.radius is px).
	light.texture_scale = float(params.get("radius", 160)) / 32.0
	light_root.add_child(light)
	player_light_node = light


func _build_glow(params: Dictionary) -> void:
	# 2D bloom: WorldEnvironment with glow over the canvas. Needs
	# rendering/viewport/hdr_2d=true — shipped statically in the template
	# project.godot (glow is silently inert without it).
	var env := Environment.new()
	env.background_mode = Environment.BG_CANVAS
	env.glow_enabled = true
	env.glow_intensity = float(params.get("intensity", 0.8))
	var world_env := WorldEnvironment.new()
	world_env.environment = env
	light_root.add_child(world_env)


func _build_tiles() -> void:
	tile_nodes.clear()
	for y in range(grid_h):
		for x in range(grid_w):
			var slot := int(terrain[y][x])
			if str(slot_category.get(slot, "empty")) == "empty":  # background shows
				continue
			var sprite := Sprite2D.new()
			sprite.texture = slot_atlas[slot]
			sprite.centered = false
			sprite.texture_filter = tile_filter
			sprite.position = Vector2(x, y) * CELL
			sprite.scale = Vector2(CELL, CELL) / sprite.texture.get_size()
			# Translucent water: volume tiles show the parallax behind
			# them — draw only; the physics `volumes` map is untouched.
			if str(slot_category.get(slot, "")) == "volume":
				sprite.modulate.a = water_alpha
			world_root.add_child(sprite)
			# Remember a breakable tile's node so the fuse can free it on break.
			if breakable_types.has(int(grid[y][x])):
				tile_nodes[Vector2i(x, y)] = sprite


func _build_markers(triggers: Array) -> void:
	# VISIBLE gameplay props (playtest ask: the exit had no graphic so
	# levels read as continuing past a teleport, and checkpoints were
	# abstract boxes). Generated prop sprites when the art track produced
	# them (manifest "props"), drawn placeholder shapes otherwise. No
	# exit-goal doorway is drawn anymore (postmortem ticket 3): the win
	# zone is the whole column/summit, so the single-cell doorway was a
	# false affordance. The checkpoint flag still raises its pennant; a
	# full-height finish-line over the exit column is the future marker.
	var props: Dictionary = manifest.get("props", {}).get(stage_id, {})
	checkpoints.clear()
	room_marks.clear()
	for t in triggers:
		var kind := str(t.get("type", ""))
		if kind == "room_entrance" or kind == "room_portal":
			# Secret-room entrance (main map) / return portal (in-room):
			# stand on the cell + the entry verb (pipe = Down, door = Up).
			var params: Dictionary = t.get("params", {})
			var verb := str(params.get("verb", "pipe"))
			_build_room_prop(
				props, verb,
				Vector2(
					(float(t["x"]) + 0.5) * CELL, (float(t["y"]) + 1.0) * CELL
				),
			)
			room_marks.append({
				"x": int(t["x"]),
				"y": int(t["y"]),
				"kind": kind,
				"verb": verb,
				"room_id": str(params.get("room_id", "")),
				"return_x": int(params.get("return_x", int(t["x"]))),
				"return_y": int(params.get("return_y", int(t["y"]))),
			})
			continue
		if kind != "checkpoint":
			continue
		var claimed := _build_checkpoint_flag(
			str(props.get("checkpoint", "")),
			Vector2((float(t["x"]) + 0.5) * CELL, (float(t["y"]) + 1.0) * CELL),
		)
		checkpoints.append({
			"pos": Vector2(t["x"], t["y"]),
			"active": false,
			"node": claimed,  # activation flips it visible (claimed look)
		})


func _build_room_prop(props: Dictionary, verb: String, foot: Vector2) -> void:
	# A green PIPE (enter with Down) or a brown DOOR (enter with Up) —
	# prop sprite when the art track made one, drawn placeholder shape
	# otherwise (mirrors platformer_play.py's placeholders).
	var sprite := _prop_sprite(str(props.get(verb, "")), foot, CELL * 1.5)
	if sprite != null:
		world_root.add_child(sprite)
		return
	if verb == "pipe":
		var body := ColorRect.new()
		body.color = Color8(46, 160, 92)
		body.size = Vector2(CELL * 0.9, CELL * 1.1)
		body.position = Vector2(foot.x - CELL * 0.45, foot.y - CELL * 1.1)
		world_root.add_child(body)
		var lip := ColorRect.new()
		lip.color = Color8(58, 190, 110)
		lip.size = Vector2(CELL * 1.2, CELL * 0.4)
		lip.position = Vector2(foot.x - CELL * 0.6, foot.y - CELL * 1.5)
		world_root.add_child(lip)
	else:
		var door := ColorRect.new()
		door.color = Color8(122, 82, 46)
		door.size = Vector2(CELL * 0.9, CELL * 1.5)
		door.position = Vector2(foot.x - CELL * 0.45, foot.y - CELL * 1.5)
		world_root.add_child(door)
		var knob := ColorRect.new()
		knob.color = Color8(220, 190, 90)
		knob.size = Vector2(6, 6)
		knob.position = Vector2(
			foot.x + CELL * 0.45 - 10, foot.y - CELL * 0.8
		)
		world_root.add_child(knob)


func _prop_sprite(sprite_rel: String, foot: Vector2, side: float) -> Sprite2D:
	# One bottom-anchored, column-centered prop sprite, or null when the
	# art track produced nothing (caller draws its placeholder shape).
	if sprite_rel == "" or not FileAccess.file_exists("res://" + sprite_rel):
		return null
	var image := Image.load_from_file(
		ProjectSettings.globalize_path("res://" + sprite_rel)
	)
	if image == null:
		return null
	var sprite := Sprite2D.new()
	sprite.texture = ImageTexture.create_from_image(image)
	sprite.centered = false
	sprite.texture_filter = tile_filter
	sprite.scale = Vector2(side, side) / sprite.texture.get_size()
	sprite.position = Vector2(foot.x - side / 2.0, foot.y - side)
	return sprite


func _spawn_vfx(prop_name: String, foot: Vector2) -> void:
	# One-shot cosmetic puff (dust/splash/sparkle): bottom-anchored prop
	# sprite at ~CELL size, faded out by the pool below. SILENT no-op when
	# the art track made nothing — a placeholder rectangle for wispy VFX
	# reads as a bug. GODOT-ONLY; physics never reads the pool.
	var props: Dictionary = manifest.get("props", {}).get(stage_id, {})
	var sprite := _prop_sprite(str(props.get(prop_name, "")), foot, CELL)
	if sprite == null:
		return
	world_root.add_child(sprite)
	vfx.append({"node": sprite, "t": VFX_LIFE_S})


func _collect_item(it: Dictionary) -> void:
	# Apply one item's effect (touch pickup AND box pops share it).
	it["collected"] = true
	if it["node"] != null:
		it["node"].visible = false
	if str(it["kind"]) == "coin":
		coins += int((it["params"] as Dictionary).get("coin_value", 1))
	elif str(it["kind"]) == "heal":
		hearts = mini(
			max_hearts,
			hearts + int((it["params"] as Dictionary).get("heal_amount", 1)),
		)
	else:
		# Power-up: ONE held slot, a new pickup replaces the old; timed
		# kinds carry duration_s, the shield has none.
		held = {
			"kind": str(it["kind"]),
			"params": it["params"],
			"color": str(it["color"]),
		}
		held_t = float((it["params"] as Dictionary).get("duration_s", 0))
	_refresh_hearts()
	_spawn_vfx("sparkle", Vector2(
		(float(it["x"]) + 0.5) * CELL, (float(it["y"]) + 1.0) * CELL
	))
	_play_sfx("checkpoint")  # closed SFX set — reuse (v1)


func _break_box(bx: int, by: int) -> void:
	# Bump/stomp opens an item box: the tile stays SOLID but flips SPENT
	# (a dark inset overlay), and its item pops out and auto-collects.
	spent_boxes[Vector2i(bx, by)] = true
	var shade := ColorRect.new()
	shade.color = Color8(48, 36, 24)
	shade.position = Vector2(bx * CELL + 4, by * CELL + 4)
	shade.size = Vector2(CELL - 8, CELL - 8)
	world_root.add_child(shade)
	for it in level_items:
		if (
			str(it["source"]) == "box" and not bool(it["collected"])
			and int(it["x"]) == bx and int(it["y"]) == by
		):
			var pop := ColorRect.new()
			pop.color = Color(str(it["color"]))
			pop.size = Vector2(CELL * 0.4, CELL * 0.4)
			pop.position = Vector2(
				bx * CELL + CELL * 0.3, by * CELL + CELL * 0.3
			)
			world_root.add_child(pop)
			pops.append({"node": pop, "t": 0.35})
			_collect_item(it)


func _build_items() -> void:
	# Uncollected trail/reward items: the art-track sprite when one
	# exists, a small colored square otherwise (loud fallback; boxed
	# items hide inside their box tile until it breaks).
	for it in level_items:
		if str(it["source"]) == "box":
			continue
		var node: CanvasItem = null
		if str(it["sprite_path"]) != "":
			node = _prop_sprite(
				str(it["sprite_path"]),
				Vector2(
					(float(it["x"]) + 0.5) * CELL,
					(float(it["y"]) + 1.0) * CELL,
				),
				CELL * 0.7,
			)
			if node != null:
				world_root.add_child(node)
		if node == null:
			var r := ColorRect.new()
			r.color = Color(str(it["color"]))
			r.size = Vector2(CELL * 0.4, CELL * 0.4)
			r.position = Vector2(
				it["x"] * CELL + CELL * 0.3, it["y"] * CELL + CELL * 0.3
			)
			world_root.add_child(r)
			node = r
		it["node"] = node


func _build_checkpoint_flag(sprite_rel: String, foot: Vector2) -> CanvasItem:
	# foot = bottom-center of the trigger cell (flag plants on the
	# ground). Returns the CLAIMED visual, hidden until activation.
	var side := CELL * 1.5
	var base_sprite := _prop_sprite(sprite_rel, foot, side)
	if base_sprite != null:
		base_sprite.modulate = Color(0.55, 0.55, 0.6)  # unclaimed: greyed
		world_root.add_child(base_sprite)
		var claimed_sprite := _prop_sprite(sprite_rel, foot, side)
		claimed_sprite.visible = false  # full color once claimed
		world_root.add_child(claimed_sprite)
		return claimed_sprite
	# Placeholder flag: pole + pennant; grey pennant swaps to gold.
	var pole := ColorRect.new()
	pole.color = Color(0.42, 0.34, 0.28)
	pole.size = Vector2(maxf(2.0, CELL / 16.0), CELL * 1.5)
	pole.position = Vector2(foot.x - pole.size.x / 2.0, foot.y - pole.size.y)
	world_root.add_child(pole)
	var pennant_points := PackedVector2Array([
		Vector2(foot.x, foot.y - CELL * 1.5),
		Vector2(foot.x + CELL * 0.55, foot.y - CELL * 1.28),
		Vector2(foot.x, foot.y - CELL * 1.06),
	])
	var pennant := Polygon2D.new()
	pennant.polygon = pennant_points
	pennant.color = Color(0.55, 0.55, 0.6)  # unclaimed: grey
	world_root.add_child(pennant)
	var claimed := Polygon2D.new()
	claimed.polygon = pennant_points
	claimed.color = Color(1.0, 0.82, 0.29)  # claimed: gold
	claimed.visible = false
	world_root.add_child(claimed)
	return claimed


func _spawn_enemies(placements: Array) -> void:
	enemies.clear()
	for p in placements:
		var spec: Dictionary = enemy_defs[p["enemy_id"]]
		# Variant meaning resolves from the manifest vocabulary — the
		# placement only carries the NAME (§6.1 overrides).
		var variant: Dictionary = variant_defs.get(str(p.get("variant", "")), {})
		# EFFECTIVE size everywhere: definition.size x variant.size —
		# the body grows UP from its anchor cell (feet at y+1, top at
		# y+1-size), centered over its occupied columns (combat.py).
		var size := float(spec.get("size", 1.0)) * float(variant.get("size", 1.0))
		var cols := maxi(1, mini(2, int(size)))
		var side := (CELL - 4.0) * size
		var rect := _actor_visual(
			str(spec.get("sprite_path", "")),
			Color(str(spec["stats"].get("placeholder_color", "#ff00ff"))),
			Vector2(side, side),
		)
		# Bottom-anchored visual offset from the anchor cell: feet on the
		# anchor cell's floor, x-centered over the occupied columns —
		# a sized body must never sink into the ground it stands on.
		var vis_off := Vector2(cols * CELL / 2.0 - side / 2.0, CELL - 2.0 - side)
		if rect is Sprite2D:
			var vis := side * actor_scale
			vis_off = Vector2(cols * CELL / 2.0 - vis / 2.0, CELL - 2.0 - vis)
		var frame: Node2D = null
		var frame_off := Vector2.ZERO
		if "outline" in str(variant.get("visual", "")):
			if rect is Sprite2D:
				frame = _silhouette_frame(rect)
			else:
				frame = _outline_frame(Vector2(CELL, CELL) * size)
				frame_off = Vector2(
					side / 2.0 - CELL * size / 2.0, side + 2.0 - CELL * size
				)
			world_root.add_child(frame)
		world_root.add_child(rect)
		var behavior: Dictionary = (spec["behavior"] as Dictionary).duplicate()
		for key in variant.get("behavior", {}):
			behavior[key] = variant["behavior"][key]
		# Dead stats live (combat v1): hp x mults soaked by stomps,
		# damage x mults dealt in hearts — combat.py arithmetic.
		var mults: Dictionary = variant.get("stat_mults", {})
		var max_hp := float(spec["stats"].get("hp", 1)) * float(mults.get("hp", 1.0))
		enemies.append({
			"spec": spec,
			"behavior": behavior,
			"speed": float(spec["stats"].get("speed", 0))
				* float(variant.get("speed_mult", 1.0)),
			"size": size,
			"max_hp": max_hp,
			"hp": max_hp,
			"damage_hearts": maxi(1, roundi(
				float(spec["stats"].get("damage", 1))
				* float(mults.get("damage", 1.0))
			)),
			"alive": true,
			"hurt_t": 0.0,
			"dying_t": 0.0,  # death-linger countdown (frozen, no collision)
			# Aggro lock: set true when an aggressive enemy first spots the
			# player, cleared when it loses eyesight range or hits its
			# tether. Ephemeral runtime state, reset on checkpoint respawn.
			"alerted": false,
			"vis_off": vis_off,
			"pos": Vector2(p["x"], p["y"]),
			"home": Vector2(p["x"], p["y"]),
			"home_x": float(p["x"]),
			# WADING (water arc, "seabed" policy): a LAND enemy posted on
			# a submerged flat may occupy water — computed ONCE from its
			# placement cell (platformer_play.py mirrors).
			"amphibious": volumes.has(_tile(float(p["x"]), float(p["y"]))),
			# Hopper state (combat/level-picks arc): vy + grounded flag +
			# the hop cadence clock (platformer_play.py mirrors).
			"vy": 0.0,
			"hop_t": 0.0,
			"e_grounded": true,
			"dir": 1.0,
			"node": rect,
			"frame": frame,
			"frame_off": frame_off,
			# Per-state animation (art track, B4/G4): frames + clock + last
			# pos for the moving check + the last-picked-state latch (a
			# state change zeroes the clock so "once" states restart).
			# Empty when there's no atlas.json/frames.json (static).
			"anim": _load_anim(str(spec.get("sprite_path", ""))),
			"anim_t": 0.0,
			"anim_state": "",
			"anim_prev": Vector2(p["x"], p["y"]),
		})


func _build_decor(_foreground: Array) -> void:
	# Foreground decor sits in a root added AFTER the player — the player
	# passes BEHIND these pieces (§6.2 layer collapsing). Placeholder
	# diamonds are HIDDEN in-game (they read as bugs next to real art —
	# play-test note); the layer draws once decoration art lands. The
	# block review render still shows every decor record.
	if decor_root != null:
		decor_root.queue_free()
	decor_root = Node2D.new()
	add_child(decor_root)


func _outline_frame(size_px: Vector2) -> Node2D:
	# Four thin edges — a frame, not a filled box (rect-fallback enemies).
	var frame := Node2D.new()
	var edges := [
		Rect2(Vector2.ZERO, Vector2(size_px.x, 2)),
		Rect2(Vector2(0, size_px.y - 2), Vector2(size_px.x, 2)),
		Rect2(Vector2.ZERO, Vector2(2, size_px.y)),
		Rect2(Vector2(size_px.x - 2, 0), Vector2(2, size_px.y)),
	]
	for rect in edges:
		var edge := ColorRect.new()
		edge.color = Color.WHITE
		edge.position = rect.position
		edge.size = rect.size
		frame.add_child(edge)
	return frame


func _silhouette_frame(body: Sprite2D) -> Node2D:
	# Variant marker for SPRITE enemies: a white outline hugging the
	# creature's silhouette (four offset white-alpha copies behind it) —
	# any rectangle around a transparent sprite reads as a bug.
	# GOLD, not white — a white outline vanished on the pale skeletal
	# hound (frame-capture finding); gold contrasts with any sprite the
	# hue reservations allow. The color lives IN the shader: a custom
	# canvas fragment that writes COLOR discards node modulate.
	var material := ShaderMaterial.new()
	var shader := Shader.new()
	shader.code = (
		"shader_type canvas_item;\n"
		+ "void fragment() {\n"
		+ "\tCOLOR = vec4(1.0, 0.82, 0.29, texture(TEXTURE, UV).a);\n"
		+ "}"
	)
	material.shader = shader
	var frame := Node2D.new()
	for offset in [Vector2(-2, 0), Vector2(2, 0), Vector2(0, -2), Vector2(0, 2)]:
		var copy := Sprite2D.new()
		copy.texture = body.texture
		copy.centered = false
		copy.texture_filter = tile_filter
		copy.scale = body.scale
		copy.material = material
		copy.position = offset
		frame.add_child(copy)
	return frame


func _actor_visual(sprite_rel: String, fallback: Color, size_px: Vector2) -> CanvasItem:
	# Generated sprite when the art track produced one; the classic rect
	# otherwise (loud fallback upstream — a missing sprite is a warned-on
	# generation failure, never a silent hole). Sprites get the SNES
	# overdraw: drawn actor_scale times their hitbox (uniform, callers
	# anchor feet) — physics never changes. Rects stay hitbox-true.
	if sprite_rel != "" and FileAccess.file_exists("res://" + sprite_rel):
		var image := Image.load_from_file(
			ProjectSettings.globalize_path("res://" + sprite_rel)
		)
		if image != null:
			var sprite := Sprite2D.new()
			sprite.texture = ImageTexture.create_from_image(image)
			sprite.centered = false
			sprite.texture_filter = tile_filter
			sprite.scale = size_px * actor_scale / sprite.texture.get_size()
			return sprite
	var rect := ColorRect.new()
	rect.color = fallback
	rect.size = size_px
	return rect


func _load_anim(sprite_rel: String) -> Dictionary:
	# Per-state animation frames (art track, B4/G4). atlas.json beside
	# base.png wins: one packed sheet, each frame an AtlasTexture whose
	# margin re-inflates the trimmed crop to the UNTRIMMED frame_size square
	# — reported size stays the sprite_size square, so the once-set node
	# scale stays valid. Fallback: frames.json strips (base.png-sized
	# uniform cells), then the static base sprite (the loud fallback).
	# Authored left-facing frames load as "states_left" and play UNFLIPPED.
	# Returns {"states": {state: [Texture]}, "states_left": {...},
	# "durs": {state: [float_seconds, ...]}, "loops": {state: String}}.
	if sprite_rel == "":
		return {}
	var base_dir := sprite_rel.get_base_dir()
	var atlas_anim := _load_atlas_anim(base_dir)
	if not atlas_anim.is_empty():
		return atlas_anim
	var meta_path := "res://" + base_dir + "/frames.json"
	if not FileAccess.file_exists(meta_path):
		return {}
	var meta = JSON.parse_string(FileAccess.get_file_as_string(meta_path))
	if typeof(meta) != TYPE_DICTIONARY:
		return {}
	var states := {}
	var states_left := {}
	var durs := {}
	var loops := {}
	for state in meta:
		var m: Dictionary = meta[state]
		var n := int(m.get("frames", 0))
		var rel := str(m.get("path", ""))
		if n < 1 or not FileAccess.file_exists("res://" + rel):
			continue
		var texs := _strip_frames(rel, n)
		if texs.is_empty():
			continue
		var left_rel := str(m.get("path_left", ""))
		if (
			left_rel != "" and int(m.get("frames_left", 0)) == n
			and FileAccess.file_exists("res://" + left_rel)
		):
			var left := _strip_frames(left_rel, n)
			if left.size() == n:
				states_left[state] = left
		states[state] = texs
		durs[state] = _anim_durs(m, n)
		loops[state] = str(m.get("loop", "loop"))
	if states.is_empty():
		return {}
	return {
		"states": states, "states_left": states_left,
		"durs": durs, "loops": loops,
	}


func _strip_frames(rel: String, n: int) -> Array:
	# One horizontal strip sliced into n uniform base.png-sized cells.
	var image := Image.load_from_file(
		ProjectSettings.globalize_path("res://" + rel)
	)
	if image == null:
		return []
	@warning_ignore("integer_division")
	var fw := image.get_width() / n
	var fh := image.get_height()
	var texs: Array = []
	for i in range(n):
		var sub := image.get_region(Rect2i(i * fw, 0, fw, fh))
		texs.append(ImageTexture.create_from_image(sub))
	return texs


func _load_atlas_anim(base_dir: String) -> Dictionary:
	# Packed-atlas manifest (G4): {"path", "frame_size", "states": {state:
	# {"loop", "durations_ms", "frames": [rects], "frames_left": [...]}}}.
	# Garbled/missing → {} and the caller falls back to the strip path.
	var meta_path := "res://" + base_dir + "/atlas.json"
	if not FileAccess.file_exists(meta_path):
		return {}
	var meta = JSON.parse_string(FileAccess.get_file_as_string(meta_path))
	if typeof(meta) != TYPE_DICTIONARY:
		return {}
	var rel := str(meta.get("path", ""))
	var fsize: Variant = meta.get("frame_size", [])
	var sdict: Variant = meta.get("states", {})
	if (
		typeof(fsize) != TYPE_ARRAY or (fsize as Array).size() != 2
		or typeof(sdict) != TYPE_DICTIONARY
		or rel == "" or not FileAccess.file_exists("res://" + rel)
	):
		return {}
	var image := Image.load_from_file(
		ProjectSettings.globalize_path("res://" + rel)
	)
	if image == null:
		return {}
	var sheet := ImageTexture.create_from_image(image)
	var fw := int(fsize[0])
	var fh := int(fsize[1])
	var states := {}
	var states_left := {}
	var durs := {}
	var loops := {}
	for state in sdict:
		var m: Dictionary = sdict[state]
		var rects: Variant = m.get("frames", [])
		var offs := _anim_offsets(m, (rects as Array).size() if typeof(rects) == TYPE_ARRAY else 0)
		var texs := _atlas_frames(sheet, rects, fw, fh, offs)
		if texs.is_empty():
			continue
		var left := _atlas_frames(sheet, m.get("frames_left", []), fw, fh, offs)
		if left.size() == texs.size():
			states_left[state] = left
		states[state] = texs
		durs[state] = _anim_durs(m, texs.size())
		loops[state] = str(m.get("loop", "loop"))
	if states.is_empty():
		return {}
	return {
		"states": states, "states_left": states_left,
		"durs": durs, "loops": loops,
	}


func _anim_offsets(m: Dictionary, n: int) -> Array:
	# Per-frame authored nudges [[dx, dy], ...] in FRAME pixel space — a human
	# correction to where generation seated a frame. Honored only when the list
	# matches the frame count, the same contract "durations_ms" follows, so a
	# desynced hand-edit degrades to "no offsets" rather than silently shifting
	# the wrong frames. Absent => every offset is (0, 0) and the render is
	# byte-identical to a pack without the field.
	# Mirror of platformer_play._anim_offsets.
	var raw: Variant = m.get("offsets")
	var offs: Array = []
	if typeof(raw) == TYPE_ARRAY and (raw as Array).size() == n:
		for pair in raw:
			if typeof(pair) == TYPE_ARRAY and (pair as Array).size() >= 2:
				offs.append(Vector2i(int(pair[0]), int(pair[1])))
			else:
				offs.append(Vector2i(0, 0))
	else:
		for i in range(n):
			offs.append(Vector2i(0, 0))
	return offs


func _atlas_frames(sheet: Texture2D, rects: Variant, fw: int, fh: int, offsets: Array = []) -> Array:
	# Reconstitute each UNTRIMMED frame: blit the trimmed crop at its (ox, oy)
	# offset onto a transparent fw x fh square. Byte-for-byte the same
	# construction as platformer_play._atlas_frames, which is the point — both
	# surfaces must register frames identically.
	#
	# This REPLACED an AtlasTexture-with-margin approach that was meant to
	# re-inflate the crop to the full square. Measured: the vertical margin was
	# not applied, so any frame whose content did not touch the top of its
	# square drew exactly oy pixels too high — ember_hopper's `death` sat 12px
	# (of 32) high, and an actor visibly hopped between poses. pygame, which
	# always built real squares, disagreed. Real squares here too: costs a
	# little texture memory at 32px, removes a whole class of drift.
	var texs: Array = []
	if typeof(rects) != TYPE_ARRAY:
		return texs
	var sheet_image := sheet.get_image()
	if sheet_image == null:
		return texs
	for r in rects:
		if typeof(r) != TYPE_DICTIONARY:
			return []
		var w := int(r.get("w", 0))
		var h := int(r.get("h", 0))
		if w <= 0 or h <= 0:
			return []
		var frame_image := Image.create(fw, fh, false, sheet_image.get_format())
		frame_image.fill(Color(0, 0, 0, 0))
		var nudge: Vector2i = Vector2i(0, 0)
		if texs.size() < offsets.size():
			nudge = offsets[texs.size()]
		frame_image.blit_rect(
			sheet_image,
			Rect2i(int(r.get("x", 0)), int(r.get("y", 0)), w, h),
			Vector2i(int(r.get("ox", 0)) + nudge.x, int(r.get("oy", 0)) + nudge.y),
		)
		texs.append(ImageTexture.create_from_image(frame_image))
	return texs


func _anim_durs(m: Dictionary, n: int) -> Array:
	# Per-frame durations in seconds, floored at 1ms. A "durations_ms" list
	# is honored only when its length matches the frame count (the writer
	# guarantees it; a desynced hand-edit falls back loudly-uniform); old
	# entries fan the scalar duration_ms out uniformly — today's behavior
	# exactly. Mirror of platformer_play._anim_timing.
	var raw: Variant = m.get("durations_ms")
	var durs: Array = []
	if typeof(raw) == TYPE_ARRAY and (raw as Array).size() == n:
		for d in raw:
			durs.append(maxf(0.001, float(d) / 1000.0))
	else:
		var one := maxf(0.001, float(m.get("duration_ms", 120)) / 1000.0)
		for i in range(n):
			durs.append(one)
	return durs


func _walk_durs(t: float, durs: Array) -> int:
	# Cumulative-duration walk: the frame whose window contains t.
	var acc := 0.0
	for i in range(durs.size()):
		acc += float(durs[i])
		if t < acc:
			return i
	return durs.size() - 1


func _anim_index(t: float, durs: Array, loop_mode: String) -> int:
	# Pure frame-index math for one state (mirror of platformer_play's
	# _anim_index): per-frame durations walked cumulatively. "loop" (and any
	# unknown mode) wraps — uniform lists keep the legacy int(t / dur) % n
	# arithmetic bit-exact for old trees; "once" clamps at the last frame;
	# "ping_pong" walks 0..n-1..1 on the mirrored cycle. The loader floors
	# every duration at 1ms, so the sums never hit zero.
	var n := durs.size()
	if n <= 1:
		return 0
	var total := 0.0
	for d in durs:
		total += float(d)
	if loop_mode == "once":
		if t >= total:
			return n - 1
		return _walk_durs(t, durs)
	if loop_mode == "ping_pong":
		var cycle := durs.duplicate()
		var csum := total
		for j in range(n - 2, 0, -1):
			cycle.append(durs[j])
			csum += float(durs[j])
		var i := _walk_durs(fmod(t, csum), cycle)
		return i if i < n else 2 * (n - 1) - i
	var uniform := true
	for d in durs:
		if float(d) != float(durs[0]):
			uniform = false
			break
	if uniform:
		return int(t / float(durs[0])) % n
	return _walk_durs(fmod(t, total), durs)


func _anim_pick(candidates: Array, states: Dictionary) -> String:
	# Mirror of platformer_play.pick_anim_frame's state half (parity): the
	# caller builds the candidate priority list from runtime signals (enemy:
	# hurt>jump>walk>idle; player: fall/jump airborne, skid>land>walk>idle
	# grounded) — the first that exists wins. The caller also owns the
	# state-change latch that zeroes the clock so "once" states restart.
	for s in candidates:
		if states.has(s):
			return s
	return str(states.keys()[0])


func _truthy(v: Variant) -> bool:
	# Python's bool(): null/0/"" are false. A signal the engine didn't send
	# reads as false, so `{"on_ground": false}` still matches.
	if v == null:
		return false
	if v is bool:
		return v
	if v is int or v is float:
		return float(v) != 0.0
	return true


func _holds(cond: Variant, value: Variant) -> bool:
	# One signal test. Unknown ops fail CLOSED — a typo must not make a rule
	# always fire. Mirror of animation_spec._holds.
	if cond is Dictionary:
		for op in (cond as Dictionary):
			var want: Variant = (cond as Dictionary)[op]
			if op == "eq":
				if value != want:
					return false
			elif op in ["gt", "lt", "gte", "lte"]:
				if not (value is int or value is float):
					return false
				var a := float(value)
				var b := float(want)
				if op == "gt" and not (a > b):
					return false
				if op == "lt" and not (a < b):
					return false
				if op == "gte" and not (a >= b):
					return false
				if op == "lte" and not (a <= b):
					return false
			else:
				return false
		return true
	if cond is bool:
		return _truthy(value) == (cond as bool)
	return value == cond


func _eval_ladder(
	rules: Array, signals: Dictionary, tail: Array, default_why: String
) -> Array:
	# [candidates in priority order, why]. Every rule whose condition holds
	# contributes its state, in declaration order; `why` comes from the FIRST
	# match. Mirror of animation_spec.eval_ladder.
	var picked: Array = []
	var why := default_why
	var use_tail: Array = tail
	for rule in rules:
		var rd: Dictionary = rule
		var when_d: Dictionary = rd.get("when", {})
		var ok := true
		for sig in when_d:
			if not _holds(when_d[sig], signals.get(sig)):
				ok = false
				break
		if not ok:
			continue
		var state := String(rd.get("state", ""))
		if state != "" and not picked.has(state):
			picked.append(state)
		if picked.size() == 1:
			why = String(rd.get("why", default_why))
			var rt: Variant = rd.get("tail")
			if rt is Array:
				use_tail = rt
	var out: Array = picked.duplicate()
	for st in use_tail:
		if not out.has(String(st)):
			out.append(String(st))
	return [out, why]


func _spec_ladder(kind: String, signals: Dictionary) -> Array:
	# The data ladder for `kind`, or [] when this pack ships no override.
	if anim_spec.is_empty():
		return []
	var ladders: Dictionary = anim_spec.get("ladders", {})
	if not ladders.has(kind):
		return []
	var tails: Dictionary = anim_spec.get("tails", {})
	var whys: Dictionary = anim_spec.get("why", {})
	return _eval_ladder(
		ladders[kind], signals, tails.get(kind, ["idle"]),
		String(whys.get(kind, ""))
	)


func _player_anim_candidates(
	on_ground: bool, vy: float, dx: float, vx: float, land_t: float,
	submerged: bool = false
) -> Array:
	# Mirror of platformer_play.player_anim_candidates: returns
	# [candidates, why]. ONE place decides which state gameplay wants, so the
	# game, the sequence viewer and the sandbox HUD agree by construction.
	var skid_now := dx != 0.0 and signf(vx) == -dx and absf(vx) > 0.5
	var spec_res := _spec_ladder("player", {
		"on_ground": on_ground,
		"descending": vy > 0.0,
		"moving": dx != 0.0,
		"braking": skid_now,
		"landing": land_t > 0.0,
		"submerged": submerged,
		"vy": vy,
		"vx": vx,
	})
	if not spec_res.is_empty():
		return spec_res
	var cand: Array = []
	var why := ""
	if not on_ground:
		if vy > 0.0:
			cand.append("fall")
			why = "airborne · past the peak"
		else:
			why = "airborne · rising"
		cand.append("jump")
	else:
		var skid := dx != 0.0 and signf(vx) == -dx and absf(vx) > 0.5
		if skid:
			cand.append("skid")
			why = "grounded · braking against momentum"
		elif land_t > 0.0:
			why = "grounded · touchdown window"
		elif dx != 0.0:
			why = "grounded · moving"
		else:
			why = "grounded · at rest"
		if land_t > 0.0:
			cand.append("land")
		if dx != 0.0:
			cand.append("walk")
	cand.append_array(["idle", "walk"])
	return [cand, why]


func _enemy_anim_candidates(
	alive: bool, hurt_t: float, grounded: bool, moving: bool
) -> Array:
	# Mirror of platformer_play.enemy_anim_candidates.
	var spec_res := _spec_ladder("enemy", {
		"alive": alive, "hurt": hurt_t > 0.0,
		"grounded": grounded, "moving": moving, "submerged": false,
	})
	if not spec_res.is_empty():
		return spec_res
	if not alive:
		return [["death", "hurt", "idle"], "defeated"]
	var cand: Array = []
	var why := "holding position"
	if hurt_t > 0.0:
		cand.append("hurt")
		why = "recoiling from a hit"
	elif not grounded:
		why = "airborne (mid-hop)"
	elif moving:
		why = "moving along its path"
	if not grounded:
		cand.append("jump")
	if moving:
		cand.append("walk")
	cand.append_array(["idle", "walk", "hurt", "death"])
	return [cand, why]


# Sequence-viewer storyboards (PLAT_ANIM_MODE=sequence) — the SIGNALS a real
# play session would produce, walked in order, so `jump → fall → land` reads as
# ONE motion. Mirrors platformer_play.ANIM_SEQUENCE / ENEMY_SEQUENCE exactly.
# Player rows: [secs, on_ground, vy, dx, vx, land_t].
const ANIM_SEQUENCE: Array = [
	[0.8, true, 0.0, 0.0, 0.0, 0.0],
	[1.2, true, 0.0, 1.0, 4.0, 0.0],
	[0.45, false, -6.0, 1.0, 4.0, 0.0],
	[0.65, false, 5.0, 1.0, 4.0, 0.0],
	[0.25, true, 0.0, 1.0, 4.0, 0.12],
	[0.5, true, 0.0, 1.0, 4.0, 0.0],
	[0.5, true, 0.0, -1.0, 4.0, 0.0],
	[0.7, true, 0.0, 0.0, 0.0, 0.0],
]
# Enemy rows: [secs, alive, hurt_t, grounded, moving].
const ENEMY_SEQUENCE: Array = [
	[0.9, true, 0.0, true, false],
	[1.3, true, 0.0, true, true],
	[0.5, true, 0.0, false, true],
	[0.45, true, 0.2, true, true],
	[0.7, true, 0.0, true, true],
	[1.3, false, 0.0, true, false],
]


func _sequence_at(t: float, is_enemy: bool) -> Array:
	# Walk the storyboard to time t (wrapping) → [candidates, why, seg_index].
	# Mirror of platformer_play.sequence_at.
	var segs: Array = ENEMY_SEQUENCE if is_enemy else ANIM_SEQUENCE
	var total := 0.0
	for s in segs:
		total += float(s[0])
	var u := fmod(t, total) if total > 0.0 else 0.0
	var acc := 0.0
	for i in range(segs.size()):
		var seg: Array = segs[i]
		if u < acc + float(seg[0]):
			var r: Array = (
				_enemy_anim_candidates(bool(seg[1]), float(seg[2]), bool(seg[3]), bool(seg[4]))
				if is_enemy
				else _player_anim_candidates(
					bool(seg[1]), float(seg[2]), float(seg[3]), float(seg[4]), float(seg[5])
				)
			)
			return [r[0], r[1], i]
		acc += float(seg[0])
	return [["idle"], "", segs.size() - 1]


var player_vis_off := Vector2(4, 4)


func _spawn_player(preserve := false) -> void:
	if player_node != null:
		player_node.queue_free()
	player_node = _actor_visual(
		"sprite/player/base.png",
		Color(0.94, 0.94, 0.94),
		Vector2(CELL - 8, CELL - 8),
	)
	player_vis_off = Vector2(4, 4)
	# Squash/stretch anchor data + the anim latches reset with the node
	# (pygame's run_level rebuilds its locals the same way per level).
	player_base_scale = Vector2.ONE
	player_tex_size = Vector2.ZERO
	player_anim_state = ""
	land_t = 0.0
	if player_node is Sprite2D:
		# Overdrawn hero: x-centered on the hitbox cell, feet on its floor.
		var vis := (CELL - 8.0) * actor_scale
		player_vis_off = Vector2((CELL - vis) / 2.0, CELL - 4.0 - vis)
		player_base_scale = player_node.scale
		player_tex_size = player_node.texture.get_size()
	player_anim = _load_anim("sprite/player/base.png")
	add_child(player_node)
	if preserve:
		# Room switch: place only — hearts/held/enemy state carry through
		# (the caller may re-place at the arrival cell after the cache
		# restore). The full heal + un-kill trio is death/fresh-load only.
		_place_player_at(spawn)
	else:
		_respawn()


func _setup_audio(audio: Dictionary) -> void:
	# SFX stay per-stage (shared across a biome). MUSIC is resolved by
	# position in _apply_music — record the stage default here; the actual
	# (re)start happens on level load + per-frame section crossings.
	stage_music_rel = str(audio.get("music", ""))
	var sfx: Dictionary = audio.get("sfx", {})
	for event in sfx:
		var s := _load_audio_stream(str(sfx[event]))
		if s != null:
			var p := AudioStreamPlayer.new()
			p.stream = s
			add_child(p)
			sfx_players[str(event)] = p


func _active_music(cell: int) -> String:
	# Ported VERBATIM from platformer_play.py::_active_music so music switches
	# at identical cells on both surfaces: a user music_section wins (its path,
	# possibly "" = deliberate silence), else the level track, else the stage.
	for sec in level_music_sections:
		var s: Dictionary = sec
		if int(s.get("start", 0)) <= cell and cell < int(s.get("end", 0)):
			return str(s.get("music_path", ""))
	if level_music_path != "":
		return level_music_path
	return stage_music_rel


func _music_cell(pos: Vector2) -> int:
	return int(pos.y if layout_axis == "vertical" else pos.x)


func _apply_music(cell: int) -> void:
	# (Re)start the resolved track only when it changes (gate on music_cur),
	# freeing the previous player first (no leak). Best-effort — silence on any
	# missing/failed stream. Called on level load + every frame while playing.
	var resolved := _active_music(cell)
	if resolved == music_cur:
		return
	if music_player != null:
		music_player.queue_free()
		music_player = null
	music_cur = resolved
	if resolved == "":
		return
	var stream := _load_audio_stream(resolved)
	if stream == null:
		return
	if "loop" in stream:
		stream.loop = true
	elif "loop_mode" in stream:
		stream.loop_mode = AudioStreamWAV.LOOP_FORWARD
	music_player = AudioStreamPlayer.new()
	music_player.stream = stream
	add_child(music_player)
	music_player.play()


func _load_audio_stream(rel: String) -> AudioStream:
	# Runtime load by extension — generated files aren't editor-imported.
	var path := "res://" + rel
	if not FileAccess.file_exists(path):
		push_warning("audio file missing: " + path)
		return null
	if rel.ends_with(".ogg"):
		return AudioStreamOggVorbis.load_from_file(path)
	if rel.ends_with(".mp3"):
		var mp3 := AudioStreamMP3.new()
		mp3.data = FileAccess.get_file_as_bytes(path)
		return mp3
	if rel.ends_with(".wav"):
		return AudioStreamWAV.load_from_file(path)
	push_warning("unsupported audio format: " + rel)
	return null


func _play_sfx(event: String) -> void:
	if sfx_players.has(event):
		sfx_players[event].play()


func _save_level_cache() -> void:
	# Snapshot the CURRENT map's persistent state before leaving it —
	# what a return trip restores (indices are deterministic: build order
	# matches the on-disk record order on both surfaces).
	var collected: Array = []
	for i in range(level_items.size()):
		if bool(level_items[i]["collected"]):
			collected.append(i)
	var claimed: Array = []
	for i in range(checkpoints.size()):
		if bool(checkpoints[i]["active"]):
			claimed.append(i)
	var dead: Array = []
	for i in range(enemies.size()):
		if not bool(enemies[i]["alive"]):
			dead.append(i)
	_level_caches[_current_level_id] = {
		"collected": collected,
		"spent": spent_boxes.keys(),
		"fuses": tile_fuses.duplicate(),
		"crumbled": _crumbled.duplicate(),
		"checkpoints": claimed,
		"dead": dead,
		"respawn": respawn_point,
	}


func _restore_level_cache() -> void:
	var cache: Dictionary = _level_caches.get(_current_level_id, {})
	if cache.is_empty():
		return
	for i in cache.get("collected", []):
		if i >= 0 and i < level_items.size():
			var it: Dictionary = level_items[i]
			it["collected"] = true
			if it["node"] != null:
				(it["node"] as Node2D).visible = false
	for key in cache.get("spent", []):
		var cell: Vector2i = key
		spent_boxes[cell] = true
		var shade := ColorRect.new()
		shade.color = Color8(48, 36, 24)
		shade.position = Vector2(cell.x * CELL + 4, cell.y * CELL + 4)
		shade.size = Vector2(CELL - 8, CELL - 8)
		world_root.add_child(shade)
	for key in cache.get("fuses", {}):
		tile_fuses[key] = float(cache["fuses"][key])
	for key in cache.get("crumbled", []):
		var ccell: Vector2i = key
		grid[ccell.y][ccell.x] = 0
		_crumbled.append(ccell)
		if tile_nodes.has(ccell):
			(tile_nodes[ccell] as Node).queue_free()
			tile_nodes.erase(ccell)
	for i in cache.get("checkpoints", []):
		if i >= 0 and i < checkpoints.size():
			checkpoints[i]["active"] = true
			(checkpoints[i]["node"] as Node2D).visible = true
	if cache.has("respawn"):
		respawn_point = cache["respawn"]
	for i in cache.get("dead", []):
		if i >= 0 and i < enemies.size():
			var enemy: Dictionary = enemies[i]
			enemy["hp"] = 0.0
			enemy["alive"] = false
			enemy["dying_t"] = 0.0
			(enemy["node"] as Node2D).visible = false
			if enemy["frame"] != null:
				(enemy["frame"] as Node2D).visible = false


func _enter_room(mark: Dictionary) -> void:
	# Pipe/door entry: carry the player state into the mini-level; the
	# return cell (detour = the entrance, shortcut = further along) waits
	# on this side. level_time keeps running — one level, one timer.
	_save_level_cache()
	_room_return_level = _current_level_id
	_room_return_at = Vector2(mark["return_x"], mark["return_y"])
	_in_room = true
	_load_level_by_id(str(mark["room_id"]), true)


func _return_from_room() -> void:
	_save_level_cache()
	_in_room = false
	_load_level_by_id(_room_return_level, true, _room_return_at)


func _eject_to_parent() -> void:
	# Death inside a secret room: normal death semantics at the PARENT's
	# checkpoint — hearts refill, held clears, coins persist, and killed
	# enemies return EVERYWHERE (checkpoint_enemy_reset), while
	# collected/spent stay collected (the persistence doctrine).
	_save_level_cache()
	if bool(rules.get("checkpoint_enemy_reset", true)):
		for cache in _level_caches.values():
			cache["dead"] = []
	_in_room = false
	_load_level_by_id(_room_return_level, true, "respawn")
	_reset_survival()
	_reset_enemies()
	_refresh_hearts()


func _place_player_at(point: Vector2) -> void:
	# Position/velocity + the per-arrival transients ONLY — no heal, no
	# enemy reset. The state-preserving half of a (re)spawn: the
	# multi-room switch places the player without touching survival.
	player_pos = point
	player_vy = 0.0
	player_vx = 0.0
	damage_soaked = 0.0
	iframes = 0.0
	spawn_shield = 0.0
	coyote_t = 0.0
	moved = false  # spawn grace re-engages until the first input


func _reset_survival() -> void:
	hearts = max_hearts
	held = {}
	held_t = 0.0
	air_jump_ok = false  # power-up dies with you


func _reset_enemies() -> void:
	if bool(rules.get("checkpoint_enemy_reset", true)):
		# Killed enemies come back on a checkpoint respawn — dying never
		# leaves a half-cleared level (GameRules kind).
		for enemy in enemies:
			enemy["pos"] = enemy["home"]
			enemy["dir"] = 1.0
			enemy["dir_y"] = 1.0
			enemy["alerted"] = false
			enemy["bob_t"] = 0.0
			enemy["swoop_t"] = 0.0
			enemy["swoop_dir"] = 1.0
			enemy["swoop_dep"] = 0.0
			enemy["vy"] = 0.0
			enemy["hop_t"] = 0.0
			enemy["e_grounded"] = true
			enemy["hp"] = enemy["max_hp"]
			enemy["alive"] = true
			enemy["hurt_t"] = 0.0
			enemy["dying_t"] = 0.0
			enemy["node"].visible = true
			if enemy["frame"] != null:
				enemy["frame"].visible = true


func _respawn() -> void:
	# Death/arrival: place + full heal + un-kill (the chained trio the
	# multi-room arc splits so a room switch can preserve state).
	if _in_room:
		# Death (or R) inside a secret room EJECTS to the parent's
		# checkpoint (user-locked). Deferred to the END of _process so
		# this frame trajs the dead pose — platformer_play.py defers its
		# eject to the loop bottom the same way (parity).
		if not _pending_eject:
			_play_sfx("death")
		_pending_eject = true
		return
	_place_player_at(respawn_point)
	_reset_survival()
	_reset_enemies()
	_refresh_hearts()
	_play_sfx("death")


func _note_move() -> void:
	# First input after a (re)spawn: the pre-move grace ends and the
	# timed spawn SHIELD starts — the player can act (and be chased)
	# but cannot be hurt until it runs out.
	if not moved:
		moved = true
		if _spawn_grace():
			spawn_shield = spawn_grace_s


func _hurt(cost: int) -> void:
	# One heart pool for contact and hazard hits — spawn grace, the
	# spawn shield, and i-frames gate them (volume drain is below). A
	# held SHIELD absorbs one hit of ANY size and breaks (still granting
	# the i-frames window — no instant re-hit). Mirrors platformer_play.
	if iframes > 0.0 or spawn_shield > 0.0 or (_spawn_grace() and not moved):
		return
	if not held.is_empty() and str(held.get("kind", "")) == "shield":
		held = {}
		iframes = iframes_s
		_refresh_hearts()
		_play_sfx("checkpoint")  # the ward shatters, no heart lost
		return
	hearts -= maxi(1, cost)
	iframes = iframes_s
	_refresh_hearts()
	if hearts <= 0:
		_respawn()


func _drain(amount: float) -> void:
	# Continuous volume damage: accumulate fractions, convert each whole
	# point into one heart — ignores hurt i-frames (lava keeps hurting)
	# but respects spawn grace AND the spawn shield. A held SHIELD soaks
	# one whole heart of drain, then breaks.
	if spawn_shield > 0.0 or (_spawn_grace() and not moved):
		return
	damage_soaked += amount
	while damage_soaked >= 1.0:
		damage_soaked -= 1.0
		if not held.is_empty() and str(held.get("kind", "")) == "shield":
			held = {}
			_refresh_hearts()
			_play_sfx("checkpoint")  # the ward boils away, heart kept
			continue
		hearts -= 1
		_refresh_hearts()
		if hearts <= 0:
			_respawn()
			return


func _spawn_grace() -> bool:
	return str(rules.get("spawn_grace", "until_move")) == "until_move"


func _refresh_hearts() -> void:
	# Screen-space HUD — hearts (filled current / hollow lost) + the coin
	# counter beside them (rebuilt together; children of hearts_root so
	# both map/level visibility toggles cover them).
	if hearts_root == null:
		return
	for child in hearts_root.get_children():
		child.queue_free()
	for i in range(max_hearts):
		if i < hearts:
			var filled := ColorRect.new()
			filled.color = Color8(214, 61, 74)
			filled.position = Vector2(i * 24, 0)
			filled.size = Vector2(18, 18)
			hearts_root.add_child(filled)
		else:
			var hollow := _outline_frame(Vector2(18, 18))
			hollow.modulate = Color8(110, 48, 56)
			hollow.position = Vector2(i * 24, 0)
			hearts_root.add_child(hollow)
	var coin_x := max_hearts * 24 + 12
	var coin := ColorRect.new()
	coin.color = Color8(255, 208, 64)
	coin.position = Vector2(coin_x, 0)
	coin.size = Vector2(18, 18)
	hearts_root.add_child(coin)
	var label := Label.new()
	label.text = "x %d" % coins
	label.position = Vector2(coin_x + 24, -2)
	label.add_theme_color_override("font_color", Color8(255, 224, 128))
	hearts_root.add_child(label)
	if not held.is_empty():
		var slot := ColorRect.new()
		slot.color = Color(str(held.get("color", "#ffd700")))
		slot.position = Vector2(coin_x + 80, 0)
		slot.size = Vector2(18, 18)
		hearts_root.add_child(slot)
		var held_label := Label.new()
		held_label.text = (
			"ward" if str(held.get("kind", "")) == "shield"
			else "%.0fs" % held_t
		)
		held_label.position = Vector2(coin_x + 104, -2)
		held_label.add_theme_color_override("font_color", Color8(220, 220, 230))
		hearts_root.add_child(held_label)


func _tile(x: float, y: float) -> int:
	var ix := int(x)
	var iy := int(y)
	if ix < 0 or ix >= grid_w or iy < 0 or iy >= grid_h:
		return 0
	return int(grid[iy][ix])


func _blocked_at(x: float, y: float) -> bool:
	# Both body corners AND both body rows — mid-jump the body spans two
	# rows, and sampling only the head row let players clip sideways
	# through the first column of a wall or platform tier.
	for cy in [y, y + 0.99]:
		if blocking.has(_tile(x + BODY_L, cy)) or blocking.has(_tile(x + BODY_R, cy)):
			return true
	return false


func _landing_at(x: float, y: float, prev_bottom: float) -> bool:
	for cx in [x + BODY_L, x + BODY_R]:
		var tile := _tile(cx, y)
		if blocking.has(tile):
			return true
		if one_way.has(tile) and prev_bottom <= float(int(y)):
			return true
	return false


func _volume_params(x: float, y: float) -> Variant:
	# The volume the player's body occupies, or null. Params come from the
	# tileset slot (registry data): speed_factor/gravity/impulse/dps.
	for cx in [x + BODY_L, x + BODY_R]:
		var tile := _tile(cx, y)
		if volumes.has(tile):
			return volumes[tile]
	return null


func _enemy_can_occupy(archetype: String, x: float, y: float, swim_style: String = "", amphibious: bool = false, airborne: bool = false, hazard_immune: bool = false) -> bool:
	# Terrain constraint for an enemy's next step (GameRules-aware):
	# swimmers stay in their volume (surface-riders on its TOP row); land
	# enemies keep solid footing and — unless the game is 'amphibious', or
	# 'seabed' with THIS enemy posted in water (wading, water arc) —
	# never enter a volume. NO enemy walks into a hazard or clips through
	# a solid (behavior doctrine; jumpers are v2). Mirrors
	# platformer_play.py (mechanics parity).
	var cell := _tile(x, y)
	var below := _tile(x, y + 1.0)
	if airborne:
		# AIRBORNE occupancy (a hopper mid-arc): solids/one-ways/volumes
		# block the drift, hazard veto + footing SKIPPED — the arc
		# carries it over spikes and gaps. platformer_play.py mirrors.
		return not (blocking.has(cell) or one_way.has(cell) or volumes.has(cell))
	if hazard.has(cell) and not hazard_immune:
		# Nobody strolls into spikes — except a hazard-immune variant
		# (emberborn, combat arc). platformer_play.py mirrors.
		return false
	if archetype == "swimmer":
		if not volumes.has(cell):
			return false
		if swim_style == "surface":
			return not volumes.has(_tile(x, y - 1.0))
		return true
	if archetype == "flyer":
		# Airborne: any open-air cell — a flyer ignores ground and flies
		# over gaps, but never through walls or into hazards/water.
		return not (blocking.has(cell) or one_way.has(cell) or volumes.has(cell))
	if blocking.has(cell) or one_way.has(cell):
		return false  # no clipping through terrain
	var policy := str(rules.get("enemy_water_policy", "swimmers_only"))
	if volumes.has(cell) and policy != "amphibious" and not (policy == "seabed" and amphibious):
		return false
	return blocking.has(below) or one_way.has(below)  # no cliff-walking


func _in_sight(archetype: String, facing: float, rel: Vector2, aggro_range: float) -> bool:
	# Is the player (rel = player - enemy) both within eyesight RANGE and
	# inside this locomotion's field of view? FOV shapes are data
	# (rules.enemy_sight, per archetype): "omni" 360, "hemisphere" the
	# 180 forward half-plane (above AND below), "forward" a narrow cone in
	# front within `vband` rows, "none" blind. An archetype absent from the
	# map defaults to "omni". platformer_play.py mirrors this — parity.
	if rel.length() > aggro_range:
		return false
	var sight: Dictionary = rules.get("enemy_sight", {})
	var cfg: Dictionary = sight.get(archetype, {})
	var fov := str(cfg.get("fov", "omni"))
	if fov == "none":
		return false
	if fov == "omni":
		return true
	if rel.x * facing < 0.0:
		return false  # behind the enemy's facing half-plane
	if fov == "forward":
		return absf(rel.y) <= float(cfg.get("vband", 2))
	return true  # "hemisphere": forward half-plane, any vertical offset


func _aggro_mode(enemy: Dictionary, archetype: String) -> String:
	# The locomotion-agnostic aggro decision, shared by ground/water/air.
	# Detection is FOV-gated; then an `alerted` lock commits the chase by
	# RANGE (a locked predator tracks through its FOV, not around it) until
	# the player leaves eyesight range OR the tether (leash_range; <=0 = no
	# tether, a hunter/relentless) snaps. Returns "chase" | "return" |
	# "patrol" and mutates enemy.alerted. Mirrored in platformer_play.py.
	var behavior: Dictionary = enemy["behavior"]
	var aggro := float(behavior.get("aggro_range", 0))
	var pos: Vector2 = enemy["pos"]
	var home: Vector2 = enemy["home"]
	var rel: Vector2 = player_pos - pos
	var leash := float(behavior.get("leash_range", 0))
	# A flyer's territory (leash + return threshold) is HORIZONTAL — its dives
	# dip in Y and must not count as "straying from home". Ground/water use the
	# full 2D distance (ground Y is locked anyway).
	var home_dist := absf(pos.x - home.x) if archetype == "flyer" \
		else (pos - home).length()
	if bool(enemy.get("alerted", false)):
		if rel.length() > aggro or (leash > 0.0 and home_dist >= leash):
			enemy["alerted"] = false
	elif _in_sight(archetype, float(enemy["dir"]), rel, aggro):
		enemy["alerted"] = true
	if bool(enemy.get("alerted", false)):
		return "chase"
	if home_dist > float(behavior.get("patrol_range", 4)):
		return "return"  # chased out of its beat — walk home before pacing
	return "patrol"


func _ground_toward(enemy: Dictionary, pos: Vector2, target_x: float, step: float) -> Vector2:
	# Ground pursuit/return: step X toward a target, occupancy-gated (halts
	# at cliffs, walls, hazards, water — no clipping). Y is locked.
	var archetype := str(enemy["spec"].get("archetype", ""))
	var dir_to := signf(target_x - pos.x)
	if dir_to != 0.0:
		enemy["dir"] = dir_to
	var nx: float = pos.x + dir_to * step
	if absf(target_x - pos.x) < step:
		nx = target_x
	if _enemy_can_occupy(
		archetype, nx, pos.y, "",
		bool(enemy.get("amphibious", false)), false,
		bool((enemy["behavior"] as Dictionary).get("hazard_immune", false))
	):
		pos.x = nx
	return pos


func _swim_toward(enemy: Dictionary, pos: Vector2, target: Vector2, step: float) -> Vector2:
	# Water pursuit/return: step X and Y (independently, occupancy-gated)
	# toward a target, staying inside the volume. While hunting a swimmer
	# ignores its passive swim_style drift rule — it just needs to be in
	# water (swim_style "" occupancy).
	var dir_x := signf(target.x - pos.x)
	if dir_x != 0.0:
		enemy["dir"] = dir_x
	var nx: float = pos.x + dir_x * step
	if absf(target.x - pos.x) < step:
		nx = target.x
	if _enemy_can_occupy("swimmer", nx, pos.y, ""):
		pos.x = nx
	var ny: float = pos.y + signf(target.y - pos.y) * step
	if absf(target.y - pos.y) < step:
		ny = target.y
	if _enemy_can_occupy("swimmer", pos.x, ny, ""):
		pos.y = ny
	return pos


func _fly_toward(enemy: Dictionary, pos: Vector2, target: Vector2, step: float) -> Vector2:
	# Airborne pursuit/return: step X and Y (independently, occupancy-gated)
	# toward a target through open air — the swoop and the climb home.
	var dir_x := signf(target.x - pos.x)
	if dir_x != 0.0:
		enemy["dir"] = dir_x
	var nx: float = pos.x + dir_x * step
	if absf(target.x - pos.x) < step:
		nx = target.x
	if _enemy_can_occupy("flyer", nx, pos.y):
		pos.x = nx
	var ny: float = pos.y + signf(target.y - pos.y) * step
	if absf(target.y - pos.y) < step:
		ny = target.y
	if _enemy_can_occupy("flyer", pos.x, ny):
		pos.y = ny
	return pos


func _process(delta: float) -> void:
	if Input.is_key_pressed(KEY_ESCAPE):
		get_tree().quit()
	# Any-key gates only fire once every key has been RELEASED since the
	# state began — the keypress that opened a state can't also close it.
	if not input_armed and not Input.is_anything_pressed():
		input_armed = true
	match game_state:
		GameState.MAP:
			_map_process(delta)
			return
		GameState.START:
			if input_armed and Input.is_anything_pressed():
				_dismiss_overlay()
				level_time = 0.0
				game_state = GameState.PLAYING
			return
		GameState.END:
			if input_armed and Input.is_anything_pressed():
				_enter_map()
			return
		GameState.ANIM_PREVIEW:
			# Art review, not gameplay: no physics, no level, ESC (handled
			# above) is the only way out.
			_anim_preview_process(delta)
			return
		GameState.SPLASH:
			# Hold, then fade (the VFX pool's countdown-modulate idiom);
			# any key once armed skips straight to the map. _enter_map
			# frees the splash layer on every path.
			splash_t -= delta
			if splash_t <= 0.0 or (input_armed and Input.is_anything_pressed()):
				_enter_map()
			elif splash_t <= SPLASH_FADE_S and splash_view != null:
				splash_view.modulate.a = splash_t / SPLASH_FADE_S
			return
	if Input.is_key_pressed(KEY_R):
		# Full fresh restart of the map LEVEL — persistent room/level
		# caches for this session are discarded with it.
		_level_caches.clear()
		_in_room = false
		_pending_eject = false
		_load_level(level_index)
		level_time = 0.0
		return
	level_time += delta

	# Live music switch: crossing into/out of a user music_section swaps the
	# track (gated, cosmetic — never touches physics). Mirrors platformer_play.
	_apply_music(_music_cell(player_pos))

	var volume: Variant = _volume_params(player_pos.x, player_pos.y)
	# Volume-ENTRY edge (prev-frame latch, the was_airborne idiom): one
	# splash per plunge, at the player's foot. Cosmetic only.
	if volume != null and not in_volume_prev:
		_spawn_vfx("splash", Vector2(
			(player_pos.x + 0.5) * CELL, (player_pos.y + 1.0) * CELL
		))
	in_volume_prev = volume != null

	# --- player: same integration as the pygame harness (run-up momentum) ---
	_play_frame += 1
	# Scripted single-frame input (PLAT_ACTIONS): the action for THIS frame
	# (zero-based like pygame's frame_i — _play_frame was ++'d above), or "".
	var script_act := str(_actions.get(_play_frame - 1, ""))
	var dx := 0.0
	if Input.is_key_pressed(KEY_RIGHT) or Input.is_key_pressed(KEY_D):
		dx += 1.0
	if Input.is_key_pressed(KEY_LEFT) or Input.is_key_pressed(KEY_A):
		dx -= 1.0
	var run_held := Input.is_key_pressed(KEY_SHIFT)
	if _hold_mode != "":  # scripted verification harness
		dx = (1.0 if _hold_mode.ends_with("right") else (-1.0 if _hold_mode.ends_with("left") else 0.0))
		run_held = _hold_mode.begins_with("run")
	if dx != 0.0:
		_note_move()  # walking ends the grace, starts the shield
	var grace: bool = _spawn_grace() and not moved
	iframes = maxf(0.0, iframes - delta)
	spawn_shield = maxf(0.0, spawn_shield - delta)
	blink_t += delta
	var run_speed := float(movement["run_speed"])
	var vol_factor := 1.0
	if volume != null:
		vol_factor = float(volume.get("speed_factor", 0.55))
	# Coyote latch (BEFORE the horizontal step, off last tick's state —
	# platformer_play.py latches at the same point in its tick order):
	# re-arm while the deterministic foot probe finds support (same idiom
	# as gmove / the fuse, never the on_ground flag), else count down.
	var coyote_s := float(movement.get("coyote_s", 0.0))
	var foot_c := int(player_pos.y + 1.01)
	var ctl := _tile(player_pos.x + BODY_L, foot_c)
	var ctr := _tile(player_pos.x + BODY_R, foot_c)
	var grounded_probe: bool = player_vy >= -0.01 and (
		blocking.has(ctl) or one_way.has(ctl)
		or blocking.has(ctr) or one_way.has(ctr)
	)
	if coyote_s > 0.0:
		if grounded_probe:
			coyote_t = coyote_s
		else:
			coyote_t = maxf(0.0, coyote_t - delta)
	if grounded_probe:
		air_jump_ok = true  # double-jump re-arms on the same probe
	# Timed power-ups count DOWN (fuse arithmetic shape); the shield has
	# no timer (duration_s 0 = held until consumed).
	if not held.is_empty() and held_t > 0.0:
		held_t = maxf(0.0, held_t - delta)
		if held_t <= 0.0:
			held = {}
			_refresh_hearts()

	# Jump BEFORE the horizontal step — platformer_play.py's contract ("vy
	# set before the horizontal step"): on a jump tick the momentum probe
	# below must already see the rising vy (air control), or the two
	# surfaces apply different accelerations for one frame per jump.
	var gravity := float(movement["gravity"])
	var jump_pressed := (
		Input.is_key_pressed(KEY_SPACE)
		or Input.is_key_pressed(KEY_UP)
		or Input.is_key_pressed(KEY_W)
	)
	# Scripted verification jump: ZERO-BASED tick like pygame's frame_i
	# (_play_frame was ++'d above), so both surfaces fire on the same frames.
	var can_air_jump: bool = (
		not held.is_empty() and str(held.get("kind", "")) == "double_jump"
		and air_jump_ok
	)
	if _hold_mode != "" and _hold_jump_every > 0 and (on_ground or coyote_t > 0.0 or can_air_jump) and (_play_frame - 1) % _hold_jump_every == 0:
		jump_pressed = true
	if script_act == "up" and volume == null:
		# Scripted Up press (dry ground only — pygame gates its scripted
		# up on `volume is None` too, so a submerged frame is inert on
		# BOTH surfaces rather than a one-sided swim stroke).
		jump_pressed = true
	var down_held := (
		Input.is_key_pressed(KEY_DOWN) or Input.is_key_pressed(KEY_S)
		or script_act == "down"
	)
	# --- secret-room entry/return (multi-room arc): stand grounded on a
	# mark + the verb press — DOOR swallows the jump (genre-standard),
	# PIPE is Down ALONE (Down+jump stays the drop-through gesture). The
	# transition ends the frame BEFORE physics: no traj line, no frame
	# tick — platformer_play.py breaks its loop at the same point. ---
	if grounded_probe and volume == null:
		for mark in room_marks:
			if int(player_pos.x) != int(mark["x"]) or int(player_pos.y) != int(mark["y"]):
				continue
			var verb := str(mark["verb"])
			if (verb == "door" and jump_pressed) or (
				verb == "pipe" and down_held and not jump_pressed
			):
				_note_move()
				if str(mark["kind"]) == "room_entrance":
					_enter_room(mark)
				else:
					_return_from_room()
				return
	if jump_pressed:
		_note_move()  # a jump ends the grace, starts the shield
		grace = _spawn_grace() and not moved
		if volume != null:
			# Submerged: small swim stroke. At the surface (open air
			# above): a full jump — the reachability validator models
			# volume exit by the normal jump rule, so the consumer must
			# actually deliver it.
			if _volume_params(player_pos.x, player_pos.y - 1.0) == null:
				player_vy = -sqrt(2.0 * gravity * (float(movement["jump_height"]) + 0.4))
			else:
				player_vy = -float(volume.get("impulse", 5.0))
		elif (
			on_ground and down_held
			and bool(rules.get("platform_drop_through", true))
			and (
				one_way.has(_tile(player_pos.x + BODY_L, player_pos.y + 1.0))
				or one_way.has(_tile(player_pos.x + BODY_R, player_pos.y + 1.0))
			)
		):
			player_pos.y += 0.06  # drop through a one-way platform
			player_vy = 0.5
			on_ground = false
			coyote_t = 0.0  # dropping is leaving, not a ledge slip
		elif on_ground or coyote_t > 0.0:
			# jump_height + headroom margin: discrete integration undershoots
			# the analytic apex, which made exact-height platforms unlandable.
			player_vy = -sqrt(2.0 * gravity * (float(movement["jump_height"]) + 0.4))
			coyote_t = 0.0  # consumed — one forgiveness per slip
			_play_sfx("jump")
		elif can_air_jump:
			# One mid-air jump while the power-up is held; re-arms on the
			# grounded probe. The reachability sim never assumes it.
			player_vy = -sqrt(2.0 * gravity * (float(movement["jump_height"]) + 0.4))
			air_jump_ok = false
			_play_sfx("jump")

	# Accelerate vx toward the held direction's target (walk, or run_speed with
	# RUN held); idle bleeds it off (the slide). Ground control > air control,
	# so speed is built on the GROUND and carried through the jump — vx is
	# never reset on jump. ground_accel 0 (old manifests) = legacy instant feel.
	if float(movement.get("ground_accel", 0.0)) > 0.0:
		# "grounded for movement" is a DETERMINISTIC grid probe (support under
		# the feet, not rising) — NOT the on_ground flag, whose sub-cell
		# landing flicker differs by a float epsilon from the pygame surface
		# and would desync run-up acceleration.
		var foot: int = int(player_pos.y + 1.01)
		var tl := _tile(player_pos.x + BODY_L, foot)
		var tr := _tile(player_pos.x + BODY_R, foot)
		var gmove: bool = player_vy >= -0.01 and (
			blocking.has(tl) or one_way.has(tl) or blocking.has(tr) or one_way.has(tr)
		)
		if dx != 0.0:
			var target: float = (run_speed if run_held else float(movement.get("walk_speed", run_speed))) * vol_factor
			if not held.is_empty() and str(held.get("kind", "")) == "run_boost":
				target *= float((held.get("params", {}) as Dictionary).get("boost_mult", 1.5))
			var desired := dx * target
			var ground_a := float(movement.get("ground_accel", 0.0))
			var brake_a := float(movement.get("brake_accel", ground_a))
			var rate: float = 0.0
			if player_vx * dx < 0.0:
				# REVERSAL — held direction opposes vx: decisive ground
				# brake, WEAK air-brake (the ONLY thing that sheds air
				# speed). Brakes toward `desired`, through 0.
				rate = (brake_a if gmove else 0.3 * brake_a) * delta
			elif gmove:
				rate = ground_a * delta  # build up AND bleed overspeed
			elif absf(player_vx) < absf(desired):
				rate = float(movement.get("air_accel", 0.0)) * delta  # air, same dir: build
			# else: air at/over target, same dir -> hold (rate stays 0.0)
			if rate != 0.0:
				player_vx = minf(player_vx + rate, desired) if player_vx < desired else maxf(player_vx - rate, desired)
		else:
			var fric: float = (float(movement.get("ground_friction", 0.0)) if gmove else float(movement.get("air_friction", 0.0))) * delta
			player_vx = maxf(0.0, player_vx - fric) if player_vx > 0.0 else minf(0.0, player_vx + fric)
	else:
		player_vx = dx * run_speed * vol_factor  # legacy instant-speed feel
	var new_x: float = player_pos.x + player_vx * delta
	if _blocked_at(new_x, player_pos.y):
		player_vx = 0.0  # ran into a wall — kill horizontal momentum
	else:
		player_pos.x = clampf(new_x, 0.0, grid_w - 1.0)

	# Landing-edge capture (presentation): airborne state BEFORE the
	# vertical step — platformer_play.py snapshots at the same point.
	var was_airborne := not on_ground
	if volume != null:
		player_vy += float(volume.get("gravity", 8.0)) * delta
		player_vy = minf(player_vy, 3.0)  # terminal sink speed
	else:
		player_vy += gravity * delta
	var prev_bottom: float = player_pos.y + 0.99
	var new_y: float = player_pos.y + player_vy * delta
	if player_vy > 0.0 and _landing_at(player_pos.x, new_y + 0.99, prev_bottom):
		player_pos.y = float(int(new_y + 0.99) - 1)
		player_vy = 0.0
		on_ground = true
	elif player_vy < 0.0 and _blocked_at(player_pos.x, new_y):
		# Head-bump: a HEAD-row corner hitting an item BOX breaks it.
		# Deterministic which-box rule (mirrored in platformer_play.py):
		# the column nearer player-center wins, ties break LEFT.
		if box_tile >= 0:
			var head := int(new_y)
			var hits: Array[int] = []
			for c in [int(player_pos.x + BODY_L), int(player_pos.x + BODY_R)]:
				if (
					c >= 0 and c < grid_w and head >= 0 and head < grid_h
					and int(grid[head][c]) == box_tile
					and not spent_boxes.has(Vector2i(c, head))
					and not hits.has(c)
				):
					hits.append(c)
			if not hits.is_empty():
				var best: int = hits[0]
				for c in hits:
					if (
						absi(c - int(player_pos.x)) < absi(best - int(player_pos.x))
						or (
							absi(c - int(player_pos.x)) == absi(best - int(player_pos.x))
							and c < best
						)
					):
						best = c
				_break_box(best, head)
		player_vy = 0.0
		on_ground = false
	else:
		player_pos.y = new_y
		on_ground = false
	land_t = maxf(0.0, land_t - delta)
	if was_airborne and on_ground:
		# Airborne → grounded EDGE: arm the land one-shot window (read by
		# the anim candidates + the landing squash; never by physics).
		land_t = LAND_ANIM_S
		_spawn_vfx("dust", Vector2(
			(player_pos.x + 0.5) * CELL, (player_pos.y + 1.0) * CELL
		))

	# Breakable floors — byte-identical to platformer_play.py: the SAME
	# deterministic foot probe (never the on_ground flag), the SAME countdown
	# arithmetic, and a sorted deduped column list. At zero the collision cell
	# is cleared (the player falls next tick) and the tile node freed.
	if not breakable_types.is_empty():
		var bfoot := int(player_pos.y + 1.01)
		if player_vy >= -0.01:
			var bcols := {}
			bcols[int(player_pos.x + BODY_L)] = true
			bcols[int(player_pos.x + BODY_R)] = true
			var sorted_cols := bcols.keys()
			sorted_cols.sort()
			for bx in sorted_cols:
				if bx < 0 or bx >= grid_w or bfoot < 0 or bfoot >= grid_h:
					continue
				if not breakable_types.has(int(grid[bfoot][bx])):
					continue
				var bkey := Vector2i(bx, bfoot)
				var rem: float = float(tile_fuses.get(bkey, break_delay_s)) - delta
				if rem <= 0.0:
					grid[bfoot][bx] = 0  # crumble — empty, player falls
					_crumbled.append(bkey)  # cache survives room trips
					tile_fuses.erase(bkey)
					if tile_nodes.has(bkey):
						(tile_nodes[bkey] as Node).queue_free()
						tile_nodes.erase(bkey)
				else:
					tile_fuses[bkey] = rem

	# Item boxes break under a STOMP too ("either works"): standing on an
	# unspent box opens it — same deterministic foot probe and sorted
	# deduped columns as the fuse (byte-parity with platformer_play.py).
	if box_tile >= 0:
		var sfoot := int(player_pos.y + 1.01)
		if player_vy >= -0.01:
			var scols := {}
			scols[int(player_pos.x + BODY_L)] = true
			scols[int(player_pos.x + BODY_R)] = true
			var sorted_scols := scols.keys()
			sorted_scols.sort()
			for sx in sorted_scols:
				if sx < 0 or sx >= grid_w or sfoot < 0 or sfoot >= grid_h:
					continue
				if (
					int(grid[sfoot][sx]) == box_tile
					and not spent_boxes.has(Vector2i(sx, sfoot))
				):
					_break_box(sx, sfoot)

	# Popped items rise briefly as they auto-collect (cosmetic,
	# fixed-dt deterministic; mirrors the pygame pop animation).
	for pop in pops.duplicate():
		pop["t"] = float(pop["t"]) - delta
		if float(pop["t"]) <= 0.0:
			(pop["node"] as Node).queue_free()
			pops.erase(pop)
		else:
			(pop["node"] as ColorRect).position.y -= 2.5 * CELL * delta

	# One-shot VFX (dust/splash/sparkle) fade out over their life, then
	# free — the pops idiom; cosmetic, never read by physics.
	for fx in vfx.duplicate():
		fx["t"] = float(fx["t"]) - delta
		if float(fx["t"]) <= 0.0:
			(fx["node"] as Node).queue_free()
			vfx.erase(fx)
		else:
			(fx["node"] as CanvasItem).modulate.a = float(fx["t"]) / VFX_LIFE_S

	# Damaging volumes (swimmable lava): drain hearts continuously —
	# every accumulated point costs one heart; the fraction resets on
	# safe ground. (The old DAMAGE_BUDGET stand-in is gone: one heart
	# pool for everything.)
	if volume != null:
		_drain(float(volume.get("damage_per_second", 0.0)) * delta)
	else:
		damage_soaked = 0.0

	if player_pos.y > grid_h + 2.0:
		_respawn()
	for corner in [player_pos.x + BODY_L, player_pos.x + BODY_R]:
		var hazard_tile := _tile(corner, player_pos.y)
		if hazard.has(hazard_tile):
			_hurt(int((hazard[hazard_tile] as Dictionary).get("damage", 1)))
			break

	# --- checkpoints: crossing one moves the respawn point (3b) ---
	# --- items: anchor-cell pickup (the checkpoint convention, mirrored
	# byte-for-byte with platformer_play.py). Collected STAYS collected
	# across respawn; boxed items wait for the break mechanic; power-up
	# kinds wait for the held-slot mechanic. ---
	for it in level_items:
		if (
			not bool(it["collected"]) and str(it["source"]) != "box"
			and int(player_pos.x) == int(it["x"])
			and int(player_pos.y) == int(it["y"])
		):
			_collect_item(it)

	for checkpoint in checkpoints:
		if checkpoint["active"]:
			continue
		var cpos: Vector2 = checkpoint["pos"]
		if int(player_pos.x) == int(cpos.x) and int(player_pos.y) == int(cpos.y):
			checkpoint["active"] = true
			checkpoint["node"].visible = true  # fill lights up when claimed
			respawn_point = cpos
			_play_sfx("checkpoint")

	# --- enemies: execute their database behavior params ---
	for enemy in enemies:
		if not enemy["alive"]:
			# Death linger: frozen in place (no patrol/chase/collision),
			# playing the death strip until the window runs out. Mirrors
			# the dead early-out in platformer_play.py's Enemy.update.
			var dying := float(enemy.get("dying_t", 0.0))
			if dying > 0.0:
				dying = maxf(0.0, dying - delta)
				enemy["dying_t"] = dying
				enemy["anim_t"] = float(enemy["anim_t"]) + delta
				var anim_d: Dictionary = enemy["anim"]
				if enemy["node"] is Sprite2D and not anim_d.is_empty():
					var st_d: Dictionary = anim_d["states"]
					var state_d := _anim_pick(["death", "hurt", "idle"], st_d)
					if state_d != str(enemy["anim_state"]):
						# State-change latch: restart the clock so "once"
						# states (death) play from frame 0 and hold. pygame
						# mirrors in _enemy_frame.
						enemy["anim_state"] = state_d
						enemy["anim_t"] = 0.0
					var texs_d: Array = st_d[state_d]
					var left_d: Dictionary = anim_d["states_left"]
					var left_used_d: bool = enemy["dir"] < 0.0 and left_d.has(state_d)
					if left_used_d:
						# Authored left-facing frames play UNFLIPPED.
						texs_d = left_d[state_d]
					enemy["node"].flip_h = enemy["dir"] < 0.0 and not left_used_d
					enemy["node"].texture = texs_d[_anim_index(
						float(enemy["anim_t"]),
						anim_d["durs"][state_d],
						str(anim_d["loops"][state_d]),
					)]
				enemy["node"].visible = true
				if dying <= 0.0:
					enemy["node"].visible = false
					if enemy["frame"] != null:
						enemy["frame"].visible = false
			continue
		var espeed := float(enemy["speed"])
		var behavior: Dictionary = enemy["behavior"]
		var archetype := str(enemy["spec"].get("archetype", "sentry"))
		var swim_style := str(behavior.get("swim_style", ""))
		var pos: Vector2 = enemy["pos"]
		enemy["hurt_t"] = maxf(0.0, float(enemy["hurt_t"]) - delta)
		# Aggro is an ORTHOGONAL tier layered on locomotion: an aggressive
		# enemy (aggro_range > 0) chases/returns via the shared decision,
		# otherwise it runs its locomotion's patrol. platformer_play.py
		# mirrors every branch — mechanics parity. No spawn-grace gate: the
		# player is untouchable during grace, so an aggressive enemy may
		# close in — that is what the shield + spawn-safety radius are for,
		# and it lets a no-input frame capture actually show the chase.
		var patrol_range := float(behavior.get("patrol_range", 4))
		var mode := "patrol"
		if float(behavior.get("aggro_range", 0)) > 0.0 and espeed > 0.0:
			mode = _aggro_mode(enemy, archetype)
		var chase := espeed * float(rules.get("chase_speed_mult", 1.5)) * delta
		var walk := espeed * delta
		if archetype == "swimmer" and espeed > 0.0:
			if mode == "chase":
				pos = _swim_toward(enemy, pos, player_pos, chase)
			elif mode == "return":
				pos = _swim_toward(enemy, pos, enemy["home"] as Vector2, walk)
			elif swim_style == "float":
				# Passive floater: diagonal drift, each axis bouncing off
				# the water's boundary independently.
				var dir_y := float(enemy.get("dir_y", 1.0))
				var step := espeed * 0.7 * delta
				var drift_x: float = pos.x + enemy["dir"] * step
				if (
					absf(drift_x - enemy["home_x"]) >= patrol_range
					or not _enemy_can_occupy(archetype, drift_x, pos.y, swim_style)
				):
					enemy["dir"] = enemy["dir"] * -1.0
				else:
					pos.x = drift_x
				var drift_y: float = pos.y + dir_y * step
				if not volumes.has(_tile(pos.x, drift_y)):
					dir_y *= -1.0
				else:
					pos.y = drift_y
				enemy["dir_y"] = dir_y
			elif swim_style == "cruise":
				# Unbounded cruiser (water arc): a straight line across
				# the whole body of water, flipping only at walls/the
				# water's edge — no patrol tether. platformer_play.py
				# mirrors this.
				var cruise_x: float = pos.x + enemy["dir"] * walk
				if _enemy_can_occupy(archetype, cruise_x, pos.y, swim_style):
					pos.x = cruise_x
				else:
					enemy["dir"] = enemy["dir"] * -1.0
			else:
				# Passive within/surface swimmer: x-bounce patrol.
				var next_x: float = pos.x + enemy["dir"] * walk
				if (
					absf(next_x - enemy["home_x"]) >= patrol_range
					or not _enemy_can_occupy(archetype, next_x, pos.y, swim_style)
				):
					enemy["dir"] = enemy["dir"] * -1.0
				else:
					pos.x = next_x
		elif archetype == "hopper" and espeed > 0.0:
			# HOPPER (combat/level-picks arc): grounded = the hop clock
			# ticks and it launches at cadence facing its mode's target;
			# airborne = gravity + AIRBORNE-mode X drift + anchor-only
			# landing on support below; falls out of the world = gone.
			# platformer_play.py + the test stepper mirror every branch.
			var hop_h := float(behavior.get("hop_height", 2))
			var hop_period := float(behavior.get("hop_period_s", 1.0))
			var hop_grav := float(movement.get("gravity", 40.0))
			if bool(enemy["e_grounded"]):
				enemy["hop_t"] = float(enemy["hop_t"]) + delta
				if mode == "chase":
					enemy["dir"] = 1.0 if player_pos.x >= pos.x else -1.0
				elif mode == "return":
					enemy["dir"] = 1.0 if enemy["home_x"] >= pos.x else -1.0
				if float(enemy["hop_t"]) >= hop_period:
					enemy["hop_t"] = 0.0
					enemy["vy"] = -sqrt(2.0 * hop_grav * (hop_h + 0.25))
					enemy["e_grounded"] = false
			else:
				# Positions QUANTIZED to the traj's 1e-3 lattice so tile-
				# boundary decisions agree across surfaces (float32
				# Vector2 vs pygame's float64). platformer_play mirrors.
				var hop_step := chase if mode == "chase" else walk
				var hop_x := snappedf(pos.x + enemy["dir"] * hop_step, 0.001)
				if _enemy_can_occupy(archetype, hop_x, pos.y, "", false, true):
					pos.x = hop_x
				else:
					enemy["dir"] = enemy["dir"] * -1.0
				enemy["vy"] = float(enemy["vy"]) + hop_grav * delta
				var hop_ny := snappedf(pos.y + float(enemy["vy"]) * delta, 0.001)
				if float(enemy["vy"]) < 0.0 and blocking.has(_tile(pos.x, hop_ny)):
					# Ceiling bonk: stop rising, fall from here.
					enemy["vy"] = 0.0
				elif float(enemy["vy"]) > 0.0 and (
					blocking.has(_tile(pos.x, hop_ny + 1.0))
					or one_way.has(_tile(pos.x, hop_ny + 1.0))
				):
					var hop_target := float(int(hop_ny + 1.0) - 1)
					if hop_ny >= hop_target:
						pos.y = hop_target
						enemy["vy"] = 0.0
						enemy["e_grounded"] = true
					else:
						pos.y = hop_ny
				else:
					pos.y = hop_ny
				if pos.y > grid_h + 2.0:
					# Hopped off the world: gone (no death linger).
					enemy["alive"] = false
					enemy["dying_t"] = 0.0
					enemy["node"].visible = false
					if enemy["frame"] != null:
						enemy["frame"].visible = false
		elif archetype == "patroller" and espeed > 0.0:
			if mode == "chase":
				pos = _ground_toward(enemy, pos, player_pos.x, chase)
			elif mode == "return":
				pos = _ground_toward(enemy, pos, (enemy["home"] as Vector2).x, walk)
			else:
				# Passive / not-alerted: x-bounce patrol within its beat.
				var next_x: float = pos.x + enemy["dir"] * walk
				if (
					absf(next_x - enemy["home_x"]) >= patrol_range
					or not _enemy_can_occupy(
						archetype, next_x, pos.y, "",
						bool(enemy.get("amphibious", false)), false,
						bool((behavior as Dictionary).get("hazard_immune", false))
					)
				):
					enemy["dir"] = enemy["dir"] * -1.0
				else:
					pos.x = next_x
		elif archetype == "flyer" and espeed > 0.0:
			var fcfg: Dictionary = rules.get("flyer", {})
			var home: Vector2 = enemy["home"]
			# Flyer clocks advance EVERY frame (pure frame-count) so the bob
			# and dive phases stay deterministic across surfaces — parity.
			var bob_t := float(enemy.get("bob_t", 0.0)) + delta
			enemy["bob_t"] = bob_t
			var swoop_t := float(enemy.get("swoop_t", 0.0)) + delta
			enemy["swoop_t"] = swoop_t
			var bob := sin(
				bob_t * float(fcfg.get("hover_freq", 3.0))
			) * float(fcfg.get("hover_amp", 0.4))
			if mode == "chase":
				# Dive-bomb from altitude, "hunt from above": RECOVER on the
				# plane (bob + reposition toward the player, COMMIT the next
				# dive's dir+depth), then a fixed-direction parabolic PLUNGE
				# aimed where the player was, back up to the plane. Never
				# descends to ground-chase. platformer_play.py mirrors this.
				var period := float(fcfg.get("swoop_period", 3.0))
				var dur := float(fcfg.get("swoop_duration", 1.0))
				var phase: float = fmod(swoop_t, period)
				if phase < dur:  # DIVE (committed dir + depth)
					var u: float = phase / dur
					var sdir := float(enemy.get("swoop_dir", 1.0))
					if _enemy_can_occupy(archetype, pos.x + sdir * chase, pos.y):
						pos.x += sdir * chase
					var ny: float = home.y + float(enemy.get("swoop_dep", 0.0)) * 4.0 * u * (1.0 - u)
					if _enemy_can_occupy(archetype, pos.x, ny):
						pos.y = ny
					if sdir != 0.0:
						enemy["dir"] = sdir
				else:  # RECOVER on the plane: track player at altitude, aim next dive
					enemy["swoop_dir"] = signf(player_pos.x - pos.x)
					enemy["swoop_dep"] = maxf(0.0, player_pos.y - home.y)
					var nx: float = pos.x + signf(player_pos.x - pos.x) * walk
					if absf(player_pos.x - pos.x) < walk:
						nx = player_pos.x
					if _enemy_can_occupy(archetype, nx, pos.y):
						pos.x = nx
					if _enemy_can_occupy(archetype, pos.x, home.y + bob):
						pos.y = home.y + bob
					if player_pos.x != pos.x:
						enemy["dir"] = signf(player_pos.x - pos.x)
			elif mode == "return":
				pos = _fly_toward(enemy, pos, home, walk)  # climb home
			elif float(behavior.get("aggro_range", 0)) > 0.0:
				# AGGRESSIVE flyer idle: hover near spawn — a vertical bob plus a
				# horizontal sway that scans its 180 cone both ways (and drifts
				# back into the hover zone if it ended a chase outside it).
				var sway := float(fcfg.get("hover_sway", 2.0))
				var sway_speed := float(fcfg.get("sway_speed", 1.5))
				var nx: float = pos.x + enemy["dir"] * sway_speed * delta
				if nx > enemy["home_x"] + sway:
					enemy["dir"] = -1.0
				elif nx < enemy["home_x"] - sway:
					enemy["dir"] = 1.0
				nx = pos.x + enemy["dir"] * sway_speed * delta
				if _enemy_can_occupy(archetype, nx, pos.y):
					pos.x = nx
				else:
					enemy["dir"] = enemy["dir"] * -1.0
				var by: float = home.y + bob
				if _enemy_can_occupy(archetype, pos.x, by):
					pos.y = by
			else:
				# PASSIVE flyer: horizontal patrol at altitude + a periodic
				# ambient dive (swoop) that returns to altitude.
				var next_x: float = pos.x + enemy["dir"] * walk
				if (
					absf(next_x - enemy["home_x"]) >= patrol_range
					or not _enemy_can_occupy(archetype, next_x, pos.y)
				):
					enemy["dir"] = enemy["dir"] * -1.0
				else:
					pos.x = next_x
				var dur := float(fcfg.get("swoop_duration", 1.0))
				var phase: float = fmod(swoop_t, float(fcfg.get("swoop_period", 3.0)))
				var dip := 0.0
				if phase < dur:
					dip = float(fcfg.get("swoop_depth", 3.0)) * sin(PI * phase / dur)
				var sy: float = home.y + dip
				if _enemy_can_occupy(archetype, pos.x, sy):
					pos.y = sy
				elif _enemy_can_occupy(archetype, pos.x, home.y):
					pos.y = home.y
		# sentry: stationary by definition
		enemy["pos"] = pos
		enemy["node"].position = pos * CELL + enemy["vis_off"]
		if enemy["node"] is Sprite2D:
			enemy["node"].flip_h = enemy["dir"] < 0.0
		# Per-state frame playback (B4/G4): advance the clock, pick the state
		# off the same runtime signals as pygame (hurt_t / hopper-airborne /
		# moving), swap the texture. Frames report the untrimmed sprite_size
		# square (strips are base.png-sized; atlas frames re-inflate via
		# margin) so the node scale stays valid; an empty anim dict leaves
		# the static base sprite (loud fallback).
		var anim: Dictionary = enemy["anim"]
		if enemy["node"] is Sprite2D and not anim.is_empty():
			var moving: bool = (pos - enemy["anim_prev"]).length() > 0.0001
			enemy["anim_prev"] = pos
			enemy["anim_t"] = float(enemy["anim_t"]) + delta
			var st: Dictionary = anim["states"]
			var eres: Array = _enemy_anim_candidates(
				true, float(enemy["hurt_t"]), bool(enemy["e_grounded"]), moving
			)
			var cand: Array = eres[0]
			var state := _anim_pick(cand, st)
			if state != str(enemy["anim_state"]):
				# State-change latch: restart the clock so "once" states
				# play from frame 0. pygame mirrors in _enemy_frame.
				enemy["anim_state"] = state
				enemy["anim_t"] = 0.0
			var texs: Array = st[state]
			var eleft: Dictionary = anim["states_left"]
			var eleft_used: bool = enemy["dir"] < 0.0 and eleft.has(state)
			if eleft_used:
				# Authored left-facing frames play UNFLIPPED (asymmetric art).
				texs = eleft[state]
			enemy["node"].flip_h = enemy["dir"] < 0.0 and not eleft_used
			var idx := _anim_index(
				float(enemy["anim_t"]),
				anim["durs"][state],
				str(anim["loops"][state]),
			)
			enemy["node"].texture = texs[idx]
		# Stomp flash: a surviving stomp blinks the body briefly.
		enemy["node"].visible = not (
			float(enemy["hurt_t"]) > 0.0 and int(enemy["hurt_t"] * 20.0) % 2 == 0
		)
		if enemy["frame"] != null:
			# Silhouette frames track the body exactly; border frames sit
			# offset around the rect (their stored offset).
			enemy["frame"].position = enemy["node"].position + enemy["frame_off"]
			if enemy["node"] is Sprite2D:
				for copy in enemy["frame"].get_children():
					copy.flip_h = enemy["dir"] < 0.0
		# Size-aware touch AABB (combat.py occupancy): the body is `size`
		# cells square, bottom-anchored — feet at y+1, top at y+1-size,
		# centered over its occupied columns.
		var esize := float(enemy["size"])
		var cols := maxi(1, mini(2, int(esize)))
		var e_cx: float = pos.x + cols / 2.0
		var e_top: float = pos.y + 1.0 - esize
		var e_bottom: float = pos.y + 1.0
		var overlap_x: bool = absf((player_pos.x + 0.5) - e_cx) < 0.35 + esize / 2.0
		var overlap_y: bool = player_pos.y < e_bottom and player_pos.y + 1.0 > e_top
		if not (overlap_x and overlap_y):
			continue
		if player_vy > 0.0 and player_pos.y + 1.0 <= e_top + 0.35:
			# STOMP: falling onto the head band — damage the enemy,
			# bounce off it. No i-frames spent; stomping is safe.
			enemy["hp"] = float(enemy["hp"]) - stomp_damage
			if float(enemy["hp"]) <= 0.0:
				enemy["alive"] = false
				# Death linger: freeze in place playing the death strip
				# from its first frame; vanish when the window runs out
				# (0 for old manifests = the vanish-on-kill-frame feel).
				# platformer_play.py's Enemy.stomp mirrors both resets.
				var linger := float(rules.get("death_linger_s", 0.0))
				enemy["dying_t"] = linger
				enemy["anim_t"] = 0.0
				if linger <= 0.0:
					enemy["node"].visible = false
					if enemy["frame"] != null:
						enemy["frame"].visible = false
				_play_sfx("jump")  # bounce impulse; no stomp SFX in v1
			else:
				enemy["hurt_t"] = 0.25
			var gravity_b := float(movement["gravity"])
			var jump_v := sqrt(2.0 * gravity_b * (float(movement["jump_height"]) + 0.4))
			# BOUNCE V2 (combat arc): jump HELD at the stomp = a full jump
			# off the head; not held = the damped hop. Dedicated jump_held
			# signal (real held poll OR the scripted "jumphold" action)
			# feeding ONLY this bounce — platformer_play.py mirrors.
			var jump_held := (
				Input.is_key_pressed(KEY_SPACE)
				or Input.is_key_pressed(KEY_UP)
				or Input.is_key_pressed(KEY_W)
				or script_act == "jumphold"
			)
			player_vy = -jump_v * (1.0 if jump_held else stomp_bounce)
		else:
			_hurt(int(enemy["damage_hearts"]))

	if _traj_file != null:
		var parts := PackedStringArray()
		for e in enemies:
			var ep: Vector2 = e["pos"]
			parts.append("%s:%.3f:%.3f:%d" % [
				str(e["spec"].get("enemy_id", "")), ep.x, ep.y,
				(1 if bool(e.get("alerted", false)) else 0),
			])
		# PLAYER token first (P:px:py:vx:coins), matching the pygame
		# harness, so player-movement AND pickup parity are diffable.
		var pl := "P:%.3f:%.3f:%.3f:%d" % [
			player_pos.x, player_pos.y, player_vx, coins,
		]
		_traj_file.store_line("%d|%s|%s" % [_traj_frame, pl, ",".join(parts)])
		_traj_file.flush()
		_traj_frame += 1

	player_node.position = player_pos * CELL + player_vis_off
	if player_light_node != null:
		# player_light follows the body center (visual only — the light
		# reads the position, physics never reads the light).
		player_light_node.position = player_pos * CELL + Vector2(
			CELL / 2.0, CELL / 2.0
		)
	if dx != 0.0:
		player_facing = dx  # face the direction of travel
	player_anim_t += delta
	if player_node is Sprite2D:
		# Squash & stretch (godot-only, VISUAL only): node-scale around the
		# bottom-center anchor — a fast rise stretches, the land window
		# squashes and relaxes. Physics and the pygame surface untouched.
		var sx := 1.0
		var sy := 1.0
		if not on_ground and player_vy < -6.0:
			sx = 0.92
			sy = 1.08
		elif land_t > 0.0:
			var lu := land_t / LAND_ANIM_S
			sx = lerpf(1.0, 1.18, lu)
			sy = lerpf(1.0, 0.84, lu)
		player_node.scale = Vector2(
			player_base_scale.x * sx, player_base_scale.y * sy
		)
		player_node.position.x += (
			player_tex_size.x * player_base_scale.x * (1.0 - sx) / 2.0
		)
		player_node.position.y += (
			player_tex_size.y * player_base_scale.y * (1.0 - sy)
		)
		player_node.flip_h = player_facing < 0.0
		# Per-state playback (B4/G4): fall past the peak / jump on the rise
		# airborne; skid (braking against carried momentum) > land (the
		# one-shot window) > walk grounded; idle/walk tail. Frames report
		# the untrimmed sprite_size square (strips are base.png-sized;
		# atlas frames re-inflate via margin) so the node scale stays
		# valid. Empty anim → the static base sprite stays (loud fallback).
		if not player_anim.is_empty():
			var pst: Dictionary = player_anim["states"]
			# The ladder lives in _player_anim_candidates — shared with the
			# sequence viewer and the sandbox HUD.
			var pres: Array = _player_anim_candidates(
				on_ground, player_vy, dx, player_vx, land_t, volume != null
			)
			var pcand: Array = pres[0]
			player_anim_why = String(pres[1])
			var pstate := _anim_pick(pcand, pst)
			if sandbox_hud != null:
				# The ladder with the winner marked — a pick below the head
				# means that state has no art and the game fell through.
				var lad: PackedStringArray = []
				var win_i := pcand.find(pstate)
				for j in range(pcand.size()):
					lad.append(("[%s]" % pcand[j]) if j == win_i else String(pcand[j]))
				sandbox_hud.text = "state  %s\n%s\n%s\nsandbox · no win condition · R reset" % [
					pstate, player_anim_why, " > ".join(lad)
				]
			if pstate != player_anim_state:
				# State-change latch: restart the clock so "once" states
				# (jump/land) play from frame 0. pygame mirrors inline.
				player_anim_state = pstate
				player_anim_t = 0.0
			var ptexs: Array = pst[pstate]
			var pleft: Dictionary = player_anim["states_left"]
			if player_facing < 0.0 and pleft.has(pstate):
				# Authored left-facing frames play UNFLIPPED (asymmetric art).
				ptexs = pleft[pstate]
				player_node.flip_h = false
			player_node.texture = ptexs[_anim_index(
				player_anim_t,
				player_anim["durs"][pstate],
				str(player_anim["loops"][pstate]),
			)]
	# Spawn grace / shield / i-frames: the player BLINKS (intermittently
	# invisible) the whole time they are untouchable.
	player_node.visible = not (
		(grace or spawn_shield > 0.0 or iframes > 0.0)
		and int(blink_t * 8.0) % 2 == 0
	)
	camera.position = Vector2(
		(player_pos.x + 0.5) * CELL, (player_pos.y + 0.5) * CELL
	)

	# --- exit: horizontal → the whole exit COLUMN (leave right); vertical →
	# the exit ROW at the summit, any column (climb to the top). Mirrors
	# platformer_play.py (parity). ---
	var reached_exit := (
		int(player_pos.y) <= int(exit_cell.y)
		if layout_axis == "vertical"
		else int(player_pos.x) == int(exit_cell.x)
	)
	# WIN SUPPRESSION inside a secret room: its exit cell is the return
	# PORTAL, not a goal — without this gate, nearing the portal would
	# mark the PARENT level beaten and jump to the world map.
	if _in_room:
		reached_exit = false
	# The SANDBOX has no win condition (PLAT_SANDBOX): the exit is just
	# another cell, so movement can keep being felt instead of the run
	# ending the moment you touch it. Mirrors platformer_play.
	if sandbox_mode:
		reached_exit = false
	if not won and reached_exit:
		won = true
		_play_sfx("win")
		var lid := str(level_ids[level_index])
		beaten[lid] = true
		_save_progress()  # durable immediately, even if the player quits
		_show_overlay(
			"Congratulations!",
			"%s cleared in %s — any key for the world map" % [
				str(level_display.get(lid, lid)), _format_time(level_time),
			],
		)
		game_state = GameState.END
		input_armed = false
	# A death-in-room eject switches maps BETWEEN frames — this frame
	# already traj'd the dead pose (platformer_play.py defers the same
	# way at its loop bottom).
	if _pending_eject:
		_pending_eject = false
		_eject_to_parent()
