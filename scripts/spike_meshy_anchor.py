#!/usr/bin/env python3
"""D4 spike — pixel art through Meshy: mesh + auto-rig quality check.

Runs 1-4 pack characters through Meshy image-to-3d (multi-image when you give
several views) and optionally auto-rigging, then downloads every GLB/thumbnail
and writes a report you judge by eye.

PAID + USER-RUN. Nothing is submitted until you confirm (or pass --yes).
Meshy bills in credits on YOUR key: textured image-to-3d is typically ~15-20
credits per mesh and rigging ~5-10 — check your dashboard for the real rates.

Request shapes mirror poseforge/lib/meshy.ts (proven working 2026-07):
  POST  openapi/v1/image-to-3d        {image_url, should_texture, should_remesh,
                                       target_polycount, target_formats:["glb"]}
  POST  openapi/v1/multi-image-to-3d  {image_urls[<=4], ...same}
  POST  openapi/v1/rigging            {input_task_id, height_meters}
  GET   openapi/v1/<kind>/<task_id>   -> {status: PENDING|IN_PROGRESS|SUCCEEDED|
                                          FAILED|CANCELED, progress, model_urls,
                                          result.rigged_character_glb_url, ...}

Usage:
  export MESHY_API_KEY=...
  .venv/bin/python scripts/spike_meshy_anchor.py out/meshy_spike \\
      "player=<pack>/sprite/player/base.png" \\
      "amber_moth=<pack>/sprite/enemy/amber_moth/base.png" \\
      "hero_sheet=front.png,side.png,back.png"

  name=img.png            -> image-to-3d
  name=a.png,b.png,c.png  -> multi-image-to-3d (first 4 used)

Flags:
  --no-rig          skip the rigging leg (mesh quality only)
  --no-upscale      send images as-is (default: NEAREST-upscale so the long
                    side is >=512px — keeps pixel art crisp for Meshy)
  --height 1.7      rig height_meters
  --yes             skip the confirm prompt
  --poll 6          poll interval seconds

What to judge afterwards (open the GLBs in Blender / a three.js viewer / Godot):
  mesh:  silhouette reads? limbs separated or fused? front/back consistent?
         texture usable or mush? scale sane?
  rig:   SUCCEEDED at all (creatures are expected to fail — that is data)?
         bones where limbs are? T-pose recoverable? Mixamo-style names?
The point of the spike: does RAW upscaled pixel art mesh acceptably, or does
the anchor path need a painterly restyle pass before Meshy (the open D4 bet).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MESHY_BASE = "https://api.meshy.ai/openapi/"
TARGET_POLYCOUNT = 30000  # poseforge's setting
UPSCALE_MIN_SIDE = 512


# ---------------------------------------------------------------- http helpers

def _request(method: str, url: str, key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(
            f"Meshy {method} {url} -> HTTP {e.code}\n{detail}\n"
            "(If the field names drifted, compare poseforge/lib/meshy.ts "
            "against Meshy's current API docs.)"
        )


def submit(kind: str, key: str, body: dict) -> str:
    out = _request("POST", f"{MESHY_BASE}v1/{kind}", key, body)
    task_id = out.get("result")
    if not isinstance(task_id, str):
        raise SystemExit(f"Unexpected submit response for {kind}: {out}")
    return task_id


def poll(kind: str, key: str, task_id: str, interval: float) -> dict:
    started = time.time()
    last = -1
    while True:
        task = _request("GET", f"{MESHY_BASE}v1/{kind}/{task_id}", key)
        status = task.get("status")
        progress = task.get("progress")
        if isinstance(progress, (int, float)) and progress != last:
            last = progress
            print(f"    {kind} {task_id[:8]}… {status} {progress}%"
                  f" · {time.time() - started:.0f}s", flush=True)
        if status == "SUCCEEDED":
            task["_elapsed_s"] = round(time.time() - started, 1)
            return task
        if status in ("FAILED", "CANCELED"):
            err = (task.get("task_error") or {}).get("message", status)
            task["_elapsed_s"] = round(time.time() - started, 1)
            task["_error"] = err
            return task
        if time.time() - started > 30 * 60:
            raise SystemExit(f"Timed out after 30m on {kind}/{task_id}")
        time.sleep(interval)


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=300) as res:
        dest.write_bytes(res.read())
    print(f"    saved {dest} ({dest.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------- image prep

def prep_image(path: Path, out_dir: Path, upscale: bool) -> str:
    """Return a data URI; NEAREST-upscale small pixel art unless disabled."""
    raw = path.read_bytes()
    suffix = path.suffix.lower().lstrip(".") or "png"
    if upscale:
        try:
            from PIL import Image
        except ImportError:
            raise SystemExit("Pillow not found — run with the canon .venv python, "
                             "or pass --no-upscale.")
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        if min(w, h) < UPSCALE_MIN_SIDE:
            factor = max(1, (UPSCALE_MIN_SIDE + min(w, h) - 1) // min(w, h))
            img = img.resize((w * factor, h * factor), Image.NEAREST)
            prepped = out_dir / f"prepped_{path.stem}_x{factor}.png"
            img.save(prepped)
            print(f"    upscaled {path.name} {w}x{h} -> {img.size[0]}x{img.size[1]}"
                  f" (NEAREST x{factor})")
            raw, suffix = prepped.read_bytes(), "png"
    b64 = base64.b64encode(raw).decode()
    return f"data:image/{'jpeg' if suffix in ('jpg', 'jpeg') else 'png'};base64,{b64}"


# ---------------------------------------------------------------- main

def main() -> None:
    args = [a for a in sys.argv[1:]]
    flags = {a for a in args if a.startswith("--") and "=" not in a}
    kv = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in args
          if a.startswith("--") and "=" in a}
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) < 2:
        print(__doc__)
        raise SystemExit(2)

    key = os.environ.get("MESHY_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set MESHY_API_KEY (meshy.ai -> Settings -> API). "
                         "Note: commercial use of outputs needs a paid Meshy tier.")

    out_root = Path(positional[0]).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    do_rig = "--no-rig" not in flags
    upscale = "--no-upscale" not in flags
    height = float(kv.get("--height", "1.7"))
    interval = float(kv.get("--poll", "6"))

    characters: list[tuple[str, list[Path]]] = []
    for spec in positional[1:]:
        if "=" not in spec:
            raise SystemExit(f"Bad spec {spec!r} — use name=img.png[,img2.png,...]")
        name, imgs = spec.split("=", 1)
        paths = [Path(p).expanduser() for p in imgs.split(",") if p]
        for p in paths:
            if not p.is_file():
                raise SystemExit(f"{name}: {p} is not a file")
        characters.append((name, paths[:4]))

    n_mesh = len(characters)
    n_rig = n_mesh if do_rig else 0
    print(f"\nPlan: {n_mesh} mesh task(s) (textured, remeshed, {TARGET_POLYCOUNT} polys)"
          f" + {n_rig} rigging task(s) at height {height}m.")
    print("This SPENDS Meshy credits on your key (ballpark ~15-20/mesh, ~5-10/rig"
          " — verify on your dashboard).")
    if "--yes" not in flags:
        if input("Proceed? [y/N] ").strip().lower() != "y":
            raise SystemExit("Aborted — nothing submitted.")

    rows = []
    for name, paths in characters:
        print(f"\n== {name} ({len(paths)} view(s)) ==")
        char_dir = out_root / name
        char_dir.mkdir(parents=True, exist_ok=True)
        uris = [prep_image(p, char_dir, upscale) for p in paths]

        if len(uris) == 1:
            kind, body = "image-to-3d", {"image_url": uris[0]}
        else:
            kind, body = "multi-image-to-3d", {"image_urls": uris}
        body.update({"should_texture": True, "should_remesh": True,
                     "target_polycount": TARGET_POLYCOUNT,
                     "target_formats": ["glb"]})

        mesh_task_id = submit(kind, key, body)
        print(f"    submitted {kind}: {mesh_task_id}")
        mesh = poll(kind, key, mesh_task_id, interval)
        row = {"name": name, "views": len(uris), "kind": kind,
               "mesh_task": mesh_task_id, "mesh_status": mesh.get("status"),
               "mesh_s": mesh.get("_elapsed_s"), "rig_status": "skipped",
               "rig_s": None, "notes": mesh.get("_error", "")}

        glb = (mesh.get("model_urls") or {}).get("glb")
        if glb:
            download(glb, char_dir / "mesh.glb")
        thumb = mesh.get("thumbnail_url")
        if isinstance(thumb, str) and thumb:
            download(thumb, char_dir / "mesh_thumb.png")

        if do_rig and mesh.get("status") == "SUCCEEDED":
            rig_task_id = submit("rigging", key,
                                 {"input_task_id": mesh_task_id,
                                  "height_meters": height})
            print(f"    submitted rigging: {rig_task_id}")
            rig = poll("rigging", key, rig_task_id, interval)
            row["rig_status"] = rig.get("status")
            row["rig_s"] = rig.get("_elapsed_s")
            if rig.get("_error"):
                row["notes"] = (row["notes"] + " · " if row["notes"] else "") + \
                    f"rig: {rig['_error']}"
            rigged = (rig.get("result") or {}).get("rigged_character_glb_url")
            if rigged:
                download(rigged, char_dir / "rigged.glb")
            (char_dir / "rig_task.json").write_text(json.dumps(rig, indent=2))
        (char_dir / "mesh_task.json").write_text(json.dumps(mesh, indent=2))
        rows.append(row)

    lines = [
        "# Meshy anchor spike — results\n",
        f"Ran {len(rows)} character(s) · rig leg: {'on' if do_rig else 'off'}"
        f" · upscale: {'on' if upscale else 'off'}\n",
        "| character | views | route | mesh | time | rig | time | notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['name']} | {r['views']} | {r['kind']} | "
                     f"{r['mesh_status']} | {r['mesh_s']}s | "
                     f"{r['rig_status']} | {r['rig_s'] or '—'} | {r['notes']} |")
    lines += [
        "\n## Judge by eye (the actual spike)",
        "Open each `mesh.glb` / `rigged.glb` in Blender, Godot, or a glTF viewer:",
        "- silhouette reads as the character? limbs separated or fused?",
        "- front/back consistent, or mirrored mush?",
        "- texture usable, or does pixel art need a painterly restyle pass first?",
        "- rig: bones where limbs are? T-pose recoverable? (creature FAILED = data)",
        "\nVerdict to record in the PRD: raw-upscaled-pixel-art path viable "
        "as tier 2/3 input, or the anchor pipeline inserts a restyle step before Meshy.",
    ]
    report = out_root / "report.md"
    report.write_text("\n".join(lines) + "\n")
    print(f"\nDone. Report: {report}")


if __name__ == "__main__":
    main()
