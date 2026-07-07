# Canon platformer slice — Godot consumer.
#
# Everything on screen resolves from the generated databases, mirroring the
# review renderer and the (throwaway) pygame harness:
#   - movement physics   <- manifest.json  (PlayerMovementSpec)
#   - tile appearance    <- tileset manifest slots + tilesheet.png regions
#   - level geometry     <- level/<stage>/<id>/collision.grid.json
#   - enemy color/stats  <- enemy/<id>.json  (placeholder_color, behavior)
#   - placements/markers <- level.json + entities.json
# No content is hardcoded here. When real art replaces the placeholder
# tilesheet, this script does not change.
#
# Controls: arrows/A-D move, Space/W/Up jump, R restart level, Esc quit.
extends Node2D

const CELL := 32.0
const TILE_EMPTY := 0
const TILE_SPIKE := 10
# Tile semantics: 1=FLOOR and 3=WALL block from all sides; 2=PLATFORM is
# one-way (land from above only) — see _blocked_at/_landing_at.

var manifest: Dictionary
var movement: Dictionary
var stage_id: String
var level_ids: Array
var level_index := 0

var grid: Array = []
var grid_w := 0
var grid_h := 0
var spawn := Vector2.ZERO
var exit_cell := Vector2.ZERO
var enemy_defs := {}
var enemies: Array = []

var player_pos := Vector2.ZERO
var player_vy := 0.0
var on_ground := false
var won := false

var tile_atlas := {}
var world_root: Node2D
var player_rect: ColorRect
var status: Label


func _ready() -> void:
	manifest = _load_json("res://manifest.json")
	movement = manifest["movement"]
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
	# Appearance resolves through the Tileset artifact: sample each slot's
	# region out of the tilesheet (placeholder squares today, art later).
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
		tile_atlas[int(slot["tile_type"])] = atlas


func _load_enemy_defs() -> void:
	for enemy_id in manifest["enemies"]:
		enemy_defs[enemy_id] = _load_json("res://enemy/%s.json" % enemy_id)


func _load_level(index: int) -> void:
	var level_id: String = level_ids[index]
	var base := "res://level/%s/%s" % [stage_id, level_id]
	var level: Dictionary = _load_json(base + "/level.json")
	grid = _load_json(base + "/collision.grid.json")["collision"]
	grid_h = grid.size()
	grid_w = grid[0].size() if grid_h > 0 else 0
	spawn = Vector2(level["spawn"][0], level["spawn"][1])
	exit_cell = Vector2(level["exit"][0], level["exit"][1])

	if world_root != null:
		world_root.queue_free()
	world_root = Node2D.new()
	add_child(world_root)
	_build_tiles()
	_build_markers()
	_spawn_enemies(_load_json(base + "/entities.json"))
	_spawn_player()
	won = false
	status.text = "%s — %s  (R restart, Esc quit)" % [manifest["world"], level_id]


func _build_tiles() -> void:
	for y in range(grid_h):
		for x in range(grid_w):
			var tile := int(grid[y][x])
			if tile == TILE_EMPTY:
				continue
			var sprite := Sprite2D.new()
			sprite.texture = tile_atlas.get(tile, tile_atlas[TILE_EMPTY])
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
		var rect := ColorRect.new()
		rect.color = Color(str(spec["stats"].get("placeholder_color", "#ff00ff")))
		rect.size = Vector2(CELL - 4, CELL - 4)
		world_root.add_child(rect)
		enemies.append({
			"spec": spec,
			"pos": Vector2(p["x"], p["y"]),
			"home_x": float(p["x"]),
			"dir": 1.0,
			"node": rect,
		})


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


# The player body spans [x + BODY_L, x + BODY_R] horizontally (in cells),
# so collision samples BOTH corners — single-point sampling let players
# clip through platform edges.
const BODY_L := 0.15
const BODY_R := 0.85


func _tile(x: float, y: float) -> int:
	var ix := int(x)
	var iy := int(y)
	if ix < 0 or ix >= grid_w or iy < 0 or iy >= grid_h:
		return TILE_EMPTY
	return int(grid[iy][ix])


func _blocked_at(x: float, y: float) -> bool:
	# Walls/floors block from every side; PLATFORM is one-way (handled
	# only in the landing check) so you can jump up through it.
	var left := _tile(x + BODY_L, y)
	var right := _tile(x + BODY_R, y)
	return left == 1 or left == 3 or right == 1 or right == 3


func _landing_at(x: float, y: float, prev_bottom: float) -> bool:
	# Falling: land on solids always; land on a PLATFORM only when the
	# feet were above its top edge last frame (classic one-way platform).
	for cx in [x + BODY_L, x + BODY_R]:
		var tile := _tile(cx, y)
		if tile == 1 or tile == 3:
			return true
		if tile == 2 and prev_bottom <= float(int(y)):
			return true
	return false


func _process(delta: float) -> void:
	if Input.is_key_pressed(KEY_ESCAPE):
		get_tree().quit()
	if Input.is_key_pressed(KEY_R):
		_load_level(level_index)
		return

	# --- player: same integration as the pygame harness ---
	var dx := 0.0
	if Input.is_key_pressed(KEY_RIGHT) or Input.is_key_pressed(KEY_D):
		dx += 1.0
	if Input.is_key_pressed(KEY_LEFT) or Input.is_key_pressed(KEY_A):
		dx -= 1.0
	var new_x: float = player_pos.x + dx * float(movement["run_speed"]) * delta
	if not _blocked_at(new_x, player_pos.y):
		player_pos.x = clampf(new_x, 0.0, grid_w - 1.0)

	var gravity := float(movement["gravity"])
	var jump_pressed := (
		Input.is_key_pressed(KEY_SPACE)
		or Input.is_key_pressed(KEY_UP)
		or Input.is_key_pressed(KEY_W)
	)
	if jump_pressed and on_ground:
		player_vy = -sqrt(2.0 * gravity * float(movement["jump_height"]))
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
	# _tile is bounds-safe (returns EMPTY off-grid), so jumping above the
	# screen can't wrap onto bottom-row pit spikes.
	if (
		_tile(player_pos.x + BODY_L, player_pos.y) == TILE_SPIKE
		or _tile(player_pos.x + BODY_R, player_pos.y) == TILE_SPIKE
	):
		_respawn()

	# --- enemies: execute their database behavior params ---
	for enemy in enemies:
		var spec: Dictionary = enemy["spec"]
		var speed := float(spec["stats"].get("speed", 0))
		var behavior: Dictionary = spec["behavior"]
		var archetype := str(spec.get("archetype", "sentry"))
		# Vector2 is a value type: read-modify-write, never mutate a
		# component through the Dictionary element.
		var pos: Vector2 = enemy["pos"]
		if archetype == "patroller" and speed > 0.0:
			pos.x += enemy["dir"] * speed * delta
			if absf(pos.x - enemy["home_x"]) >= float(behavior.get("patrol_range", 4)):
				enemy["dir"] = enemy["dir"] * -1.0
		elif archetype == "chaser" and speed > 0.0:
			if absf(player_pos.x - pos.x) <= float(behavior.get("aggro_range", 6)):
				pos.x += signf(player_pos.x - pos.x) * speed * delta
		enemy["pos"] = pos
		enemy["node"].position = pos * CELL + Vector2(2, 2)
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
