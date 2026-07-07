# Canon platformer slice — Godot consumer (Phase 3a: layered).
#
# Everything on screen resolves from the generated databases, mirroring the
# review renderer and the (throwaway) pygame harness:
#   - movement physics   <- manifest.json  (PlayerMovementSpec incl. water)
#   - tile appearance    <- TERRAIN layer (slot indices) + tilesheet regions
#   - tile physics       <- tileset slot `collision` semantics (E.3) —
#                           no hardcoded tile IDs anywhere in this script
#   - level geometry     <- level/<stage>/<id>/collision.grid.json
#   - background         <- background layer bands
#   - enemy color/stats  <- enemy/<id>.json; elite markers from placements
#   - decor              <- foreground layer, drawn IN FRONT of the player
# When real art replaces the placeholder tilesheet, this script does not
# change. Controls: arrows/A-D move, Space/W/Up jump or swim, R restart,
# Esc quit.
extends Node2D

const CELL := 32.0
const BODY_L := 0.15
const BODY_R := 0.85

var manifest: Dictionary
var movement: Dictionary
var stage_id: String
var level_ids: Array
var level_index := 0

var grid: Array = []
var terrain: Array = []
var grid_w := 0
var grid_h := 0
var spawn := Vector2.ZERO
var exit_cell := Vector2.ZERO
var enemy_defs := {}
var enemies: Array = []

# Physics semantics from tileset slot metadata (sets of tile-type ints).
var blocking := {}
var one_way := {}
var hazard := {}
var water := {}
var slot_atlas := {}
var slot_types := {}

var player_pos := Vector2.ZERO
var player_vy := 0.0
var on_ground := false
var won := false

var world_root: Node2D
var decor_root: Node2D
var player_rect: ColorRect
var status: Label


var rules := {}


func _ready() -> void:
	manifest = _load_json("res://manifest.json")
	movement = manifest["movement"]
	rules = manifest.get("rules", {})
	stage_id = manifest["stage_id"]
	level_ids = manifest["levels"]
	status = $Status
	_load_tileset()
	_load_enemy_defs()
	_load_level(level_index)


func _load_json(path: String) -> Variant:
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		push_error("canon: missing %s — run the slice generator first." % path)
		return {}
	return JSON.parse_string(file.get_as_text())


func _load_tileset() -> void:
	# Appearance AND physics resolve through the Tileset artifact.
	var ts: Dictionary = _load_json("res://tileset/%s/manifest.json" % stage_id)
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
		slot_types[index] = tile_type
		match str(slot.get("collision", "")):
			"solid":
				blocking[tile_type] = true
			"one_way":
				one_way[tile_type] = true
			"hazard":
				hazard[tile_type] = true
			"water":
				water[tile_type] = true


func _load_enemy_defs() -> void:
	for enemy_id in manifest["enemies"]:
		enemy_defs[enemy_id] = _load_json("res://enemy/%s.json" % enemy_id)


func _load_level(index: int) -> void:
	var level_id: String = level_ids[index]
	var base := "res://level/%s/%s" % [stage_id, level_id]
	var level: Dictionary = _load_json(base + "/level.json")
	grid = _load_json(base + "/collision.grid.json")["collision"]
	terrain = _load_json(base + "/terrain.grid.json")["terrain"]
	var background: Array = _load_json(base + "/background.grid.json")["background"]
	grid_h = grid.size()
	grid_w = grid[0].size() if grid_h > 0 else 0
	spawn = Vector2(level["spawn"][0], level["spawn"][1])
	exit_cell = Vector2(level["exit"][0], level["exit"][1])
	get_window().size = Vector2i(int(grid_w * CELL), int(grid_h * CELL))

	if world_root != null:
		world_root.queue_free()
	world_root = Node2D.new()
	add_child(world_root)
	_build_background(background)
	_build_tiles()
	_build_markers()
	_spawn_enemies(_load_json(base + "/entities.json"))
	_spawn_player()
	_build_decor(level.get("foreground", []))
	won = false
	status.text = "%s — %s  (R restart, Esc quit)" % [manifest["world"], level_id]
	move_child(status, get_child_count() - 1)


func _build_background(background: Array) -> void:
	# One rect per horizon band — placeholder art, but a real layer.
	var band_start := 0
	for y in range(1, background.size() + 1):
		var ended := y == background.size()
		if ended or int(background[y][0]) != int(background[band_start][0]):
			var band := int(background[band_start][0])
			var shade := 24 + 7 * (2 - band)
			var rect := ColorRect.new()
			rect.color = Color8(shade, shade, shade + 10)
			rect.position = Vector2(0, band_start * CELL)
			rect.size = Vector2(grid_w * CELL, (y - band_start) * CELL)
			world_root.add_child(rect)
			band_start = y


func _build_tiles() -> void:
	for y in range(grid_h):
		for x in range(grid_w):
			var slot := int(terrain[y][x])
			if slot_types.get(slot, 0) == 0:  # EMPTY slot: background shows
				continue
			var sprite := Sprite2D.new()
			sprite.texture = slot_atlas[slot]
			sprite.centered = false
			sprite.position = Vector2(x, y) * CELL
			sprite.scale = Vector2(CELL, CELL) / sprite.texture.get_size()
			world_root.add_child(sprite)


func _build_markers() -> void:
	var marker := ColorRect.new()
	marker.color = Color("40ff70")
	marker.position = exit_cell * CELL + Vector2(4, 4)
	marker.size = Vector2(CELL - 8, CELL - 8)
	world_root.add_child(marker)


func _spawn_enemies(placements: Array) -> void:
	enemies.clear()
	for p in placements:
		var spec: Dictionary = enemy_defs[p["enemy_id"]]
		var elite: bool = bool(p.get("elite", false))
		if elite:
			# Elite variation marker (§6.1 overrides): white frame.
			var frame := ColorRect.new()
			frame.color = Color.WHITE
			frame.size = Vector2(CELL, CELL)
			world_root.add_child(frame)
			var rect := _enemy_rect(spec)
			world_root.add_child(rect)
			enemies.append(_enemy_state(p, spec, rect, frame))
		else:
			var rect := _enemy_rect(spec)
			world_root.add_child(rect)
			enemies.append(_enemy_state(p, spec, rect, null))


func _enemy_rect(spec: Dictionary) -> ColorRect:
	var rect := ColorRect.new()
	rect.color = Color(str(spec["stats"].get("placeholder_color", "#ff00ff")))
	rect.size = Vector2(CELL - 4, CELL - 4)
	return rect


func _enemy_state(p: Dictionary, spec: Dictionary, rect: ColorRect, frame) -> Dictionary:
	return {
		"spec": spec,
		"pos": Vector2(p["x"], p["y"]),
		"home_x": float(p["x"]),
		"dir": 1.0,
		"node": rect,
		"frame": frame,
	}


func _build_decor(foreground: Array) -> void:
	# Foreground decor sits in a root added AFTER the player — the player
	# passes BEHIND these pieces (§6.2 layer collapsing).
	if decor_root != null:
		decor_root.queue_free()
	decor_root = Node2D.new()
	add_child(decor_root)
	for d in foreground:
		var poly := Polygon2D.new()
		var cx := float(d["x"]) * CELL + CELL / 2.0
		var cy := float(d["y"]) * CELL + CELL / 2.0
		poly.polygon = PackedVector2Array([
			Vector2(cx, cy - 10), Vector2(cx + 8, cy),
			Vector2(cx, cy + 10), Vector2(cx - 8, cy),
		])
		poly.color = Color8(185, 195, 205)
		decor_root.add_child(poly)


func _spawn_player() -> void:
	if player_rect != null:
		player_rect.queue_free()
	player_rect = ColorRect.new()
	player_rect.color = Color(0.94, 0.94, 0.94)
	player_rect.size = Vector2(CELL - 8, CELL - 8)
	add_child(player_rect)
	_respawn()


func _respawn() -> void:
	player_pos = spawn
	player_vy = 0.0


func _tile(x: float, y: float) -> int:
	var ix := int(x)
	var iy := int(y)
	if ix < 0 or ix >= grid_w or iy < 0 or iy >= grid_h:
		return 0
	return int(grid[iy][ix])


func _blocked_at(x: float, y: float) -> bool:
	return blocking.has(_tile(x + BODY_L, y)) or blocking.has(_tile(x + BODY_R, y))


func _landing_at(x: float, y: float, prev_bottom: float) -> bool:
	for cx in [x + BODY_L, x + BODY_R]:
		var tile := _tile(cx, y)
		if blocking.has(tile):
			return true
		if one_way.has(tile) and prev_bottom <= float(int(y)):
			return true
	return false


func _in_water(x: float, y: float) -> bool:
	return water.has(_tile(x + BODY_L, y)) or water.has(_tile(x + BODY_R, y))


func _enemy_can_occupy(archetype: String, x: float, y: float) -> bool:
	# Terrain constraint for an enemy's next step (GameRules-aware):
	# swimmers stay in their pool; land enemies keep solid footing and —
	# unless the game is 'amphibious' — never enter water.
	var cell := _tile(x, y)
	var below := _tile(x, y + 1.0)
	if archetype == "swimmer":
		return water.has(cell)
	if water.has(cell) and str(rules.get("enemy_water_policy", "swimmers_only")) != "amphibious":
		return false
	return blocking.has(below) or one_way.has(below)  # no cliff-walking


func _process(delta: float) -> void:
	if Input.is_key_pressed(KEY_ESCAPE):
		get_tree().quit()
	if Input.is_key_pressed(KEY_R):
		_load_level(level_index)
		return

	var swimming := _in_water(player_pos.x, player_pos.y)

	# --- player: same integration as the pygame harness ---
	var dx := 0.0
	if Input.is_key_pressed(KEY_RIGHT) or Input.is_key_pressed(KEY_D):
		dx += 1.0
	if Input.is_key_pressed(KEY_LEFT) or Input.is_key_pressed(KEY_A):
		dx -= 1.0
	var speed := float(movement["run_speed"])
	if swimming:
		speed *= float(movement.get("water_speed_factor", 0.55))
	var new_x: float = player_pos.x + dx * speed * delta
	if not _blocked_at(new_x, player_pos.y):
		player_pos.x = clampf(new_x, 0.0, grid_w - 1.0)

	var gravity := float(movement["gravity"])
	var jump_pressed := (
		Input.is_key_pressed(KEY_SPACE)
		or Input.is_key_pressed(KEY_UP)
		or Input.is_key_pressed(KEY_W)
	)
	var down_held := Input.is_key_pressed(KEY_DOWN) or Input.is_key_pressed(KEY_S)
	if jump_pressed:
		if swimming:
			player_vy = -float(movement.get("swim_impulse", 5.0))
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
		elif on_ground:
			# jump_height + headroom margin: discrete integration undershoots
			# the analytic apex, which made exact-height platforms unlandable.
			player_vy = -sqrt(2.0 * gravity * (float(movement["jump_height"]) + 0.4))
	if swimming:
		player_vy += float(movement.get("water_gravity", 8.0)) * delta
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
		player_vy = 0.0
		on_ground = false
	else:
		player_pos.y = new_y
		on_ground = false

	if player_pos.y > grid_h + 2.0:
		_respawn()
	if (
		hazard.has(_tile(player_pos.x + BODY_L, player_pos.y))
		or hazard.has(_tile(player_pos.x + BODY_R, player_pos.y))
	):
		_respawn()

	# --- enemies: execute their database behavior params ---
	for enemy in enemies:
		var spec: Dictionary = enemy["spec"]
		var espeed := float(spec["stats"].get("speed", 0))
		var behavior: Dictionary = spec["behavior"]
		var archetype := str(spec.get("archetype", "sentry"))
		var pos: Vector2 = enemy["pos"]
		if (archetype == "patroller" or archetype == "swimmer") and espeed > 0.0:
			var next_x: float = pos.x + enemy["dir"] * espeed * delta
			if (
				absf(next_x - enemy["home_x"]) >= float(behavior.get("patrol_range", 4))
				or not _enemy_can_occupy(archetype, next_x, pos.y)
			):
				enemy["dir"] = enemy["dir"] * -1.0
			else:
				pos.x = next_x
		elif archetype == "chaser" and espeed > 0.0:
			if absf(player_pos.x - pos.x) <= float(behavior.get("aggro_range", 6)):
				var next_x: float = pos.x + signf(player_pos.x - pos.x) * espeed * delta
				if _enemy_can_occupy(archetype, next_x, pos.y):
					pos.x = next_x  # halts at water/cliff edges
		enemy["pos"] = pos
		enemy["node"].position = pos * CELL + Vector2(2, 2)
		if enemy["frame"] != null:
			enemy["frame"].position = pos * CELL
		if pos.distance_to(player_pos) < 0.7:
			_respawn()

	player_rect.position = player_pos * CELL + Vector2(4, 4)

	# --- exit ---
	if not won and int(player_pos.x) == int(exit_cell.x) and int(player_pos.y) == int(exit_cell.y):
		won = true
		if level_index + 1 < level_ids.size():
			level_index += 1
			_load_level(level_index)
		else:
			status.text = "%s — WORLD COMPLETE (R to replay, Esc to quit)" % manifest["world"]
