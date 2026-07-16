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
# World flow (multi-stage): a DK/SMW-style WORLD MAP (manifest
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

# --- world flow: MAP -> START -> PLAYING -> END -> MAP ---
enum GameState { MAP, START, PLAYING, END }
var game_state: int = GameState.MAP
var map_root: CanvasLayer
var overlay_root: CanvasLayer
var map_nodes: Array = []  # manifest world_map nodes (play order)
var map_selected := 0
var map_move_cool := 0.0
var input_armed := false  # any-key gates fire only after all keys release
var beaten := {}  # level_id -> true; persisted per seed
var level_time := 0.0
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
var save_path := ""

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
var enemies: Array = []
var checkpoints: Array = []

# Generated audio (late audio phase, manifest["audio"]): a looping music
# player + one player per SFX event. Missing/failed audio = silence —
# the game never depends on it.
var music_player: AudioStreamPlayer = null
var sfx_players := {}

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
# Player animation (art track): idle/walk/JUMP, loaded at spawn. Empty → the
# static base sprite plays (loud fallback).
var player_anim: Dictionary = {}
var player_anim_t := 0.0
var player_facing := 1.0
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
var effects_root: CanvasLayer
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
	var combat: Dictionary = manifest.get("combat", {})
	max_hearts = int(combat.get("player_max_hearts", 3))
	stomp_damage = int(combat.get("stomp_damage", 6))
	stomp_bounce = float(combat.get("stomp_bounce_factor", 0.7))
	iframes_s = float(combat.get("hurt_iframes_s", 1.0))
	spawn_grace_s = float(combat.get("spawn_grace_s", 1.0))
	status = $UI/Status
	# Hearts HUD on the UI CanvasLayer (screen-space — a world node would
	# scale and drift under camera zoom), below the status line.
	hearts_root = Control.new()
	hearts_root.position = Vector2(16, 44)
	$UI.add_child(hearts_root)
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
	_enter_map()


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
	for node in [world_root, backdrop_root, effects_root]:
		if node != null:
			node.queue_free()
	world_root = null
	backdrop_root = null
	effects_root = null
	_dismiss_overlay()
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


func _load_level(index: int) -> void:
	var level_id: String = level_ids[index]
	_enter_stage(level_stage.get(level_id, stage_id))
	var base := "res://level/%s/%s" % [stage_id, level_id]
	var level: Dictionary = _load_json(base + "/level.json")
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
	# Fresh level load = fresh item state; respawn never touches it.
	level_items.clear()
	coins = 0
	spent_boxes.clear()
	pops.clear()
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
	_spawn_player()
	_build_decor(level.get("foreground", []))
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
	if backdrop_root != null:
		backdrop_root.queue_free()
		backdrop_root = null
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
		var layer := ParallaxLayer.new()
		var depth := float(depths[i]) if i < depths.size() else 0.5
		layer.motion_scale = Vector2(depth, 1.0)
		var texture := ImageTexture.create_from_image(image)
		var s := (grid_h * CELL) / float(image.get_height())
		var band_w := image.get_width() * s
		layer.motion_mirroring = Vector2(band_w, 0)
		# Two copies side by side: mirroring alone left a void beyond one
		# band width until the camera moved (seen in frame captures).
		for copy_index in range(2):
			var sprite := Sprite2D.new()
			sprite.texture = texture
			sprite.centered = false
			sprite.texture_filter = tile_filter
			sprite.scale = Vector2(s, s)
			sprite.position = Vector2(band_w * copy_index, 0)
			layer.add_child(sprite)
		backdrop_root.add_child(layer)


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
	if stage_effects.is_empty():
		return
	effects_root = CanvasLayer.new()
	effects_root.layer = 10
	add_child(effects_root)
	for effect in stage_effects:
		if str(effect.get("name", "")) != "particles_falling":
			continue
		var params: Dictionary = effect.get("params", {})
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
			world_root.add_child(sprite)
			# Remember a breakable tile's node so the fuse can free it on break.
			if breakable_types.has(int(grid[y][x])):
				tile_nodes[Vector2i(x, y)] = sprite


func _build_markers(triggers: Array) -> void:
	# VISIBLE gameplay props (playtest ask: the exit had no graphic so
	# levels read as continuing past a teleport, and checkpoints were
	# abstract boxes). Generated prop sprites when the art track produced
	# them (manifest "props"), drawn placeholder shapes otherwise. The
	# exit ZONE stays the whole column — the goal object marks it; the
	# checkpoint flag raises its colored pennant once claimed.
	var props: Dictionary = manifest.get("props", {}).get(stage_id, {})
	_build_exit_goal(str(props.get("exit", "")))
	checkpoints.clear()
	for t in triggers:
		if str(t.get("type", "")) != "checkpoint":
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


func _build_exit_goal(sprite_rel: String) -> void:
	# The goal object standing ON the exit cell — the level visibly ENDS
	# here (you still leave through the whole column, any height).
	var foot := Vector2((exit_cell.x + 0.5) * CELL, (exit_cell.y + 1.0) * CELL)
	var sprite := _prop_sprite(sprite_rel, foot, CELL * 2.0)
	if sprite != null:
		world_root.add_child(sprite)
		return
	# Placeholder goal: a green doorway — frame + translucent glow.
	var door := Vector2(CELL * 1.1, CELL * 1.8)
	var glow := ColorRect.new()
	glow.color = Color(0.25, 1.0, 0.44, 0.35)
	glow.size = door
	glow.position = Vector2(foot.x - door.x / 2.0, foot.y - door.y)
	world_root.add_child(glow)
	var frame := _outline_frame(door)
	frame.modulate = Color(0.25, 1.0, 0.44)
	frame.position = glow.position
	world_root.add_child(frame)


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
			"dir": 1.0,
			"node": rect,
			"frame": frame,
			"frame_off": frame_off,
			# Per-state animation (art track, B4): frames + clock + last pos
			# for the moving check. Empty when there's no frames.json (static).
			"anim": _load_anim(str(spec.get("sprite_path", ""))),
			"anim_t": 0.0,
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
	# Per-state animation frames (art track, B4): frames.json beside base.png,
	# each <state>.png a horizontal strip sliced by frame count. Frames are the
	# SAME size as base.png (sprite_size square) so a texture swap keeps the
	# node scale valid. Missing/garbled → {} and the static base sprite plays
	# (the loud fallback). Returns {"states": {state: [Texture]}, "durs": {...}}.
	if sprite_rel == "":
		return {}
	var meta_path := "res://" + sprite_rel.get_base_dir() + "/frames.json"
	if not FileAccess.file_exists(meta_path):
		return {}
	var meta = JSON.parse_string(FileAccess.get_file_as_string(meta_path))
	if typeof(meta) != TYPE_DICTIONARY:
		return {}
	var states := {}
	var durs := {}
	for state in meta:
		var m: Dictionary = meta[state]
		var n := int(m.get("frames", 0))
		var rel := str(m.get("path", ""))
		if n < 1 or not FileAccess.file_exists("res://" + rel):
			continue
		var image := Image.load_from_file(
			ProjectSettings.globalize_path("res://" + rel)
		)
		if image == null:
			continue
		var fw := image.get_width() / n
		var fh := image.get_height()
		var texs: Array = []
		for i in range(n):
			var sub := image.get_region(Rect2i(i * fw, 0, fw, fh))
			texs.append(ImageTexture.create_from_image(sub))
		states[state] = texs
		durs[state] = maxf(0.001, float(m.get("duration_ms", 120)) / 1000.0)
	if states.is_empty():
		return {}
	return {"states": states, "durs": durs}


func _anim_pick(candidates: Array, states: Dictionary) -> String:
	# Mirror of platformer_play.pick_anim_frame (parity): the caller builds the
	# candidate priority list from runtime signals (enemy: hurt>walk>idle;
	# player: jump>walk>idle); the first that exists wins.
	for s in candidates:
		if states.has(s):
			return s
	return str(states.keys()[0])


var player_vis_off := Vector2(4, 4)


func _spawn_player() -> void:
	if player_node != null:
		player_node.queue_free()
	player_node = _actor_visual(
		"sprite/player/base.png",
		Color(0.94, 0.94, 0.94),
		Vector2(CELL - 8, CELL - 8),
	)
	player_vis_off = Vector2(4, 4)
	if player_node is Sprite2D:
		# Overdrawn hero: x-centered on the hitbox cell, feet on its floor.
		var vis := (CELL - 8.0) * actor_scale
		player_vis_off = Vector2((CELL - vis) / 2.0, CELL - 4.0 - vis)
	player_anim = _load_anim("sprite/player/base.png")
	add_child(player_node)
	_respawn()


func _setup_audio(audio: Dictionary) -> void:
	var music_rel = audio.get("music")
	if music_rel != null and str(music_rel) != "":
		var stream := _load_audio_stream(str(music_rel))
		if stream != null:
			if "loop" in stream:
				stream.loop = true
			elif "loop_mode" in stream:
				stream.loop_mode = AudioStreamWAV.LOOP_FORWARD
			music_player = AudioStreamPlayer.new()
			music_player.stream = stream
			add_child(music_player)
			music_player.play()
	var sfx: Dictionary = audio.get("sfx", {})
	for event in sfx:
		var s := _load_audio_stream(str(sfx[event]))
		if s != null:
			var p := AudioStreamPlayer.new()
			p.stream = s
			add_child(p)
			sfx_players[str(event)] = p


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


func _respawn() -> void:
	player_pos = respawn_point
	player_vy = 0.0
	player_vx = 0.0
	damage_soaked = 0.0
	hearts = max_hearts
	iframes = 0.0
	spawn_shield = 0.0
	coyote_t = 0.0
	held = {}
	held_t = 0.0
	air_jump_ok = false  # power-up dies with you
	moved = false  # spawn grace re-engages until the first input
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
			enemy["hp"] = enemy["max_hp"]
			enemy["alive"] = true
			enemy["hurt_t"] = 0.0
			enemy["dying_t"] = 0.0
			enemy["node"].visible = true
			if enemy["frame"] != null:
				enemy["frame"].visible = true
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


func _enemy_can_occupy(archetype: String, x: float, y: float, swim_style: String = "") -> bool:
	# Terrain constraint for an enemy's next step (GameRules-aware):
	# swimmers stay in their volume (surface-riders on its TOP row); land
	# enemies keep solid footing and — unless the game is 'amphibious' —
	# never enter a volume. NO enemy walks into a hazard or clips through
	# a solid (behavior doctrine; jumpers are v2). Mirrors
	# platformer_play.py (mechanics parity).
	var cell := _tile(x, y)
	var below := _tile(x, y + 1.0)
	if hazard.has(cell):
		return false  # nobody strolls into spikes
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
	if volumes.has(cell) and str(rules.get("enemy_water_policy", "swimmers_only")) != "amphibious":
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
	if _enemy_can_occupy(archetype, nx, pos.y):
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
	if Input.is_key_pressed(KEY_R):
		_load_level(level_index)
		level_time = 0.0
		return
	level_time += delta

	var volume: Variant = _volume_params(player_pos.x, player_pos.y)

	# --- player: same integration as the pygame harness (run-up momentum) ---
	_play_frame += 1
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
	var down_held := Input.is_key_pressed(KEY_DOWN) or Input.is_key_pressed(KEY_S)
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
			var step: float = (float(movement.get("ground_accel", 0.0)) if gmove else float(movement.get("air_accel", 0.0))) * delta
			player_vx = minf(player_vx + step, desired) if player_vx < desired else maxf(player_vx - step, desired)
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
					var texs_d: Array = st_d[state_d]
					var dur_d: float = anim_d["durs"][state_d]
					enemy["node"].texture = texs_d[
						int(float(enemy["anim_t"]) / dur_d) % texs_d.size()
					]
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
					or not _enemy_can_occupy(archetype, next_x, pos.y)
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
		# Per-state frame playback (B4): advance the clock, pick the state
		# off the same runtime signals as pygame (hurt_t / moving), swap the
		# texture. Frames are base.png-sized so the node scale stays valid;
		# an empty anim dict leaves the static base sprite (loud fallback).
		var anim: Dictionary = enemy["anim"]
		if enemy["node"] is Sprite2D and not anim.is_empty():
			var moving: bool = (pos - enemy["anim_prev"]).length() > 0.0001
			enemy["anim_prev"] = pos
			enemy["anim_t"] = float(enemy["anim_t"]) + delta
			var st: Dictionary = anim["states"]
			var cand: Array = []
			if float(enemy["hurt_t"]) > 0.0:
				cand.append("hurt")
			if moving:
				cand.append("walk")
			cand.append_array(["idle", "walk", "hurt", "death"])
			var state := _anim_pick(cand, st)
			var texs: Array = st[state]
			var dur: float = anim["durs"][state]
			var idx := int(float(enemy["anim_t"]) / dur) % texs.size()
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
			player_vy = -jump_v * stomp_bounce
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
	if dx != 0.0:
		player_facing = dx  # face the direction of travel
	player_anim_t += delta
	if player_node is Sprite2D:
		player_node.flip_h = player_facing < 0.0
		# Per-state playback (B4): airborne → jump, moving on ground → walk,
		# else idle. Empty anim → the static base sprite stays (loud fallback).
		if not player_anim.is_empty():
			var pst: Dictionary = player_anim["states"]
			var pcand: Array = []
			if not on_ground:
				pcand.append("jump")
			if dx != 0.0:
				pcand.append("walk")
			pcand.append_array(["idle", "walk"])
			var pstate := _anim_pick(pcand, pst)
			var ptexs: Array = pst[pstate]
			var pdur: float = player_anim["durs"][pstate]
			player_node.texture = ptexs[int(player_anim_t / pdur) % ptexs.size()]
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
