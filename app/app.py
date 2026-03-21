#!/usr/bin/env python3
"""pwMediaManager – Web UI to run pwMediaEnhancer and pwPosterDownloader."""

import os
import subprocess
import threading
import uuid
import json
import time
import glob
import random
import io
import re
import requests as http_requests
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, Response, jsonify, send_file, abort

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

def load_conf():
    conf = {}
    conf_path = "/boot/config/pwMediaEnhancer.conf"
    if os.path.exists(conf_path):
        with open(conf_path) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    conf[k.strip()] = v.strip().strip('"\'')
    return conf

CONF = load_conf()
TMDB_API_KEY = CONF.get("TMDB_API_KEY", os.environ.get("TMDB_API_KEY", ""))

SCRIPTS = {
    "pwMediaEnhancer": {
        "label":       "pwMediaEnhancer",
        "description": "Sortiert und benennt Filme & Serien nach Plex-Standard um.",
        "readme_url":  "https://github.com/pennywise81/pwMediaEnhancer#readme",
        "script":      "/scripts/pwMediaEnhancer/pwMediaEnhancer.sh",
        "host_script": "/mnt/user/Fileserver/scripts/pwMediaEnhancer/pwMediaEnhancer.sh",
        "log_prefix":  "pwMediaEnhancer",
        "params": [
            {"id": "dry_run",        "flag": "--dry-run",        "label": "Dry-Run (keine Änderungen)"},
            {"id": "skip_existing",  "flag": "--skip-existing",  "label": "Nur neue Inhalte"},
            {"id": "delete_empty",   "flag": "--delete-empty",   "label": "Leere Verzeichnisse löschen"},
        ],
        "radio_groups": [
            {
                "id": "content_filter",
                "label": "Verarbeitung",
                "options": [
                    {"value": "",               "label": "Alle"},
                    {"value": "--movies-only",  "label": "Nur Filme"},
                    {"value": "--series-only",  "label": "Nur Serien"},
                ],
            },
        ],
        "dir_inputs": [
            {
                "id":      "movies_dir",
                "flag":    "--movies",
                "label":   "Filme-Verzeichnis",
                "default": "/fileserver/Filme",
                "help":    "Pfad zum Ordner, der die Filmordner enthält. Jeder Unterordner ist ein Film im Format 'Titel (Jahr)'.",
            },
            {
                "id":      "series_dir",
                "flag":    "--series",
                "label":   "Serien-Verzeichnis",
                "default": "/fileserver/Serien",
                "help":    "Pfad zum Ordner, der die Serienordner enthält. Jeder Unterordner ist eine Serie mit Season-Unterordnern.",
            },
        ],
    },
    "pwPosterDownloader": {
        "label":       "pwPosterDownloader",
        "description": "Lädt Poster von ThePosterDB & fanart.tv und lädt sie in Plex hoch.",
        "readme_url":  "https://github.com/pennywise81/pwPosterDownloader#readme",
        "script":      "/scripts/pwPosterDownloader/pwPosterDownloader.sh",
        "host_script": "/mnt/user/Fileserver/scripts/pwPosterDownloader/pwPosterDownloader.sh",
        "log_prefix":  "pwPosterDownloader",
        "params": [
            {"id": "dry_run",       "flag": "--dry-run",          "label": "Dry-Run (keine Downloads/Uploads)"},
            {"id": "skip_existing", "flag": "--skip-existing",    "label": "Nur neue Inhalte (skip existing)"},
            {"id": "plex_upload",   "flag": "--plex-upload-only", "label": "Nur Plex-Upload (kein Download)"},
        ],
        "radio_groups": [
            {
                "id": "content_filter",
                "label": "Inhaltsfilter",
                "options": [
                    {"value": "",              "label": "Alle"},
                    {"value": "--movies-only", "label": "Nur Filme"},
                    {"value": "--series-only", "label": "Nur Serien"},
                ],
            },
        ],
        "dir_inputs": [
            {
                "id":      "movies_dir",
                "flag":    "--movies-dir",
                "label":   "Filme-Verzeichnis",
                "default": "/fileserver/Filme",
                "help":    "Pfad zum Ordner mit den Filmordnern, z. B. /mnt/user/Fileserver/Filme. Jeder Unterordner ist ein Film im Format 'Titel (Jahr) {imdb-ttXXX}'.",
            },
            {
                "id":      "series_dir",
                "flag":    "--series-dir",
                "label":   "Serien-Verzeichnis",
                "default": "/fileserver/Serien",
                "help":    "Pfad zum Ordner mit den Serienordnern, z. B. /mnt/user/Fileserver/Serien. Jeder Unterordner ist eine Serie mit Season-Unterordnern.",
            },
        ],
    },
    "pwKometaManager": {
        "label":       "pwKometaManager",
        "description": "Kometa — Plex Collections & Overlays.",
        "readme_url":  "https://github.com/pennywise81/pwKometaManager#readme",
        "script":      "/scripts/pwKometaManager/pwKometaManager.sh",
        "host_script": "/mnt/user/Fileserver/scripts/pwKometaManager/pwKometaManager.sh",
        "log_prefix":  "pwKometaManager",
        "config_path": "/mnt/user/appdata/kometa/config/config.yaml",
        "params": [
            {"id": "dry_run", "flag": "--dry-run", "label": "Dry-Run"},
        ],
        "radio_groups": [
            {
                "id": "content_filter",
                "label": "Bibliothek",
                "options": [
                    {"value": "",               "label": "Alle"},
                    {"value": "--movies-only",  "label": "Nur Filme"},
                    {"value": "--series-only",  "label": "Nur Serien"},
                ],
            },
        ],
        "dir_inputs": [
            {
                "id":      "movies_dir",
                "flag":    "--movies-dir",
                "label":   "Filme-Verzeichnis",
                "default": "/fileserver/Filme",
                "help":    "Pfad zum Ordner mit den Filmordnern auf Unraid, z.B. /mnt/user/Fileserver/Filme.",
            },
            {
                "id":      "series_dir",
                "flag":    "--series-dir",
                "label":   "Serien-Verzeichnis",
                "default": "/fileserver/Serien",
                "help":    "Pfad zum Ordner mit den Serienordnern auf Unraid, z.B. /mnt/user/Fileserver/Serien.",
            },
        ],
    },
}

LOGS_DIR = Path(os.environ.get("LOGS_DIR", "/logs"))
LOGS_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = LOGS_DIR / "pwmediamanager-settings.json"


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {}


def save_settings(data: dict):
    merged = load_settings()
    merged.update(data)
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2))

# ── Job state ─────────────────────────────────────────────────────────────────

jobs = {}   # job_id → {"tool": str, "status": "running"|"done"|"error", "log": Path, "started": str}
jobs_lock = threading.Lock()


def run_job(job_id: str, cmd: list[str], log_path: Path):
    with jobs_lock:
        jobs[job_id]["status"] = "running"

    try:
        with open(log_path, "w") as lf:
            lf.write(f"# Started: {datetime.now().isoformat()}\n")
            lf.write(f"# Command: {' '.join(cmd)}\n\n")
            lf.flush()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            with jobs_lock:
                jobs[job_id]["pid"] = proc.pid

            for line in proc.stdout:
                lf.write(line)
                lf.flush()

            proc.wait()
            status = "done" if proc.returncode == 0 else "error"
            lf.write(f"\n# Finished: {datetime.now().isoformat()} (exit {proc.returncode})\n")

    except Exception as e:
        status = "error"
        with open(log_path, "a") as lf:
            lf.write(f"\n# Exception: {e}\n")

    with jobs_lock:
        jobs[job_id]["status"] = status
        jobs[job_id]["pid"] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    log_files = {}
    for key, cfg in SCRIPTS.items():
        pattern = str(LOGS_DIR / f"{cfg['log_prefix']}_*.log")
        files = sorted(glob.glob(pattern), reverse=True)
        log_files[key] = [Path(f).name for f in files[:10]]

    with jobs_lock:
        current_jobs = {jid: dict(j) for jid, j in jobs.items()}

    settings = load_settings()
    return render_template("index.html", scripts=SCRIPTS, log_files=log_files,
                           jobs=current_jobs, settings=settings)


@app.route("/run/<tool>", methods=["POST"])
def run_tool(tool):
    if tool not in SCRIPTS:
        return jsonify({"error": "unknown tool"}), 404

    cfg = SCRIPTS[tool]
    cmd = ["bash", cfg["script"]]

    # Flags (checkboxes)
    for param in cfg.get("params", []):
        if request.form.get(param["id"]):
            cmd.append(param["flag"])

    # Radio groups (mutually exclusive flags, e.g. --movies-only / --series-only)
    for rg in cfg.get("radio_groups", []):
        val = request.form.get(rg["id"], "").strip()
        if val:
            cmd.append(val)

    # Directory inputs (pwMediaEnhancer: --movies DIR --series DIR)
    tool_settings = {}
    for di in cfg.get("dir_inputs", []):
        val = request.form.get(di["id"], di["default"]).strip() or di["default"]
        cmd += [di["flag"], val]
        tool_settings[f"{tool}_{di['id']}"] = val

    # Legacy positional argument (other tools)
    if "positional" in cfg:
        val = request.form.get(cfg["positional"]["id"], cfg["positional"]["default"])
        cmd.append(val)

    # Persist directory settings
    if tool_settings:
        save_settings(tool_settings)

    # Create log file
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"{cfg['log_prefix']}_{ts}.log"

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "tool":    tool,
            "status":  "starting",
            "log":     str(log_path),
            "log_name": log_path.name,
            "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pid":     None,
        }

    thread = threading.Thread(target=run_job, args=(job_id, cmd, log_path), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "log_name": log_path.name})


@app.route("/stream/<job_id>")
def stream(job_id):
    """SSE endpoint: streams log file lines as they appear."""
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        abort(404)

    log_path = Path(job["log"])

    def generate():
        # Wait for log file to appear
        for _ in range(50):
            if log_path.exists():
                break
            time.sleep(0.1)

        pos = 0
        while True:
            with jobs_lock:
                status = jobs.get(job_id, {}).get("status", "done")

            if log_path.exists():
                with open(log_path) as f:
                    f.seek(pos)
                    chunk = f.read()
                    if chunk:
                        pos += len(chunk)
                        for line in chunk.splitlines():
                            yield f"data: {json.dumps(line)}\n\n"

            if status in ("done", "error"):
                yield f"event: done\ndata: {status}\n\n"
                break

            time.sleep(0.3)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.route("/status/<job_id>")
def job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        abort(404)
    return jsonify({"status": job["status"]})


@app.route("/logs/<filename>")
def view_log(filename):
    log_path = LOGS_DIR / filename
    if not log_path.exists() or not filename.endswith(".log"):
        abort(404)
    return send_file(str(log_path), mimetype="text/plain")


@app.route("/api/jobs")
def api_jobs():
    with jobs_lock:
        return jsonify({jid: dict(j) for jid, j in jobs.items()})


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        save_settings(request.json or {})
        return jsonify({"ok": True})
    return jsonify(load_settings())


@app.route("/api/test-overlay", methods=["POST"])
def test_overlay():
    if not PIL_AVAILABLE:
        return jsonify({"error": "Pillow not installed"}), 500

    data = request.json or {}
    tool = data.get("tool", "pwKometaManager")
    overlay_type = data.get("type", "movies")

    cfg = SCRIPTS.get(tool)
    if not cfg:
        return jsonify({"error": "unknown tool"}), 404

    settings = load_settings()
    dir_key = "movies_dir" if overlay_type == "movies" else "series_dir"
    default_dir = "/fileserver/Filme" if overlay_type == "movies" else "/fileserver/Serien"
    base_dir = settings.get(f"{tool}_{dir_key}", default_dir)

    try:
        entries = [e for e in os.scandir(base_dir) if e.is_dir()]
    except Exception as e:
        return jsonify({"error": f"Cannot list directory: {e}"}), 500

    if not entries:
        return jsonify({"error": "No entries found in directory"}), 404

    entry = random.choice(entries)
    folder_name = entry.name

    match = re.match(r'^(.+?)\s*\((\d{4})\)', folder_name)
    title = match.group(1).strip() if match else folder_name
    year = match.group(2) if match else None

    search_type = "movie" if overlay_type == "movies" else "tv"
    params = {"api_key": TMDB_API_KEY, "query": title}
    if year:
        params["year"] = year

    try:
        resp = http_requests.get(
            f"https://api.themoviedb.org/3/search/{search_type}",
            params=params, timeout=10
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        return jsonify({"error": f"TMDB error: {e}"}), 500

    if not results or not results[0].get("poster_path"):
        return jsonify({"error": f"No poster found for '{title}'"}), 404

    poster_url = f"https://image.tmdb.org/t/p/w500{results[0]['poster_path']}"
    try:
        img_resp = http_requests.get(poster_url, timeout=15)
        img_resp.raise_for_status()
        img = Image.open(io.BytesIO(img_resp.content)).convert("RGBA")
    except Exception as e:
        return jsonify({"error": f"Poster download error: {e}"}), 500

    draw = ImageDraw.Draw(img)
    w, h = img.size

    # Semi-transparent bottom bar with folder title
    bar_h = 64
    bar = Image.new("RGBA", (w, bar_h), (0, 0, 0, 190))
    img.paste(bar, (0, h - bar_h), bar)
    draw = ImageDraw.Draw(img)
    draw.text((12, h - bar_h + 10), folder_name[:60], fill=(255, 255, 255, 230))

    # "KOMETA TEST" badge at top-right
    badge_text = "KOMETA TEST"
    badge_w, badge_h = 124, 28
    bx = w - badge_w - 10
    by = 10
    try:
        draw.rounded_rectangle([bx, by, bx + badge_w, by + badge_h], radius=6, fill=(124, 58, 237, 220))
    except AttributeError:
        draw.rectangle([bx, by, bx + badge_w, by + badge_h], fill=(124, 58, 237, 220))
    draw.text((bx + 8, by + 6), badge_text, fill=(255, 255, 255, 255))

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    out.seek(0)

    response = Response(out.read(), mimetype="image/png")
    response.headers["X-Item-Name"] = folder_name
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
