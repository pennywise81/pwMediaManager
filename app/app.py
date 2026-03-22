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
import xml.etree.ElementTree as ET
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
PLEX_URL   = CONF.get("PLEX_URL",   "http://192.168.178.200:32400").rstrip("/")
PLEX_TOKEN = CONF.get("PLEX_TOKEN", "WYTd__H7hPH6a2ezqq2K")

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
        "config_path": "/mnt/user/appdata/kometa/config/config.yml",
        "params": [
            {"id": "dry_run", "flag": "--dry-run", "label": "Dry-Run"},
            {"id": "remove_overlays", "flag": "--remove-overlays", "label": "Alle Badges entfernen"},
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
    """SSE endpoint: streams log file lines as they appear, batched to avoid flooding."""
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
        batch = []
        last_flush = time.time()
        last_heartbeat = time.time()

        while True:
            with jobs_lock:
                status = jobs.get(job_id, {}).get("status", "done")

            if log_path.exists():
                with open(log_path) as f:
                    f.seek(pos)
                    chunk = f.read()
                    if chunk:
                        pos += len(chunk)
                        batch.extend(chunk.splitlines())

            now = time.time()
            is_done = status in ("done", "error")

            # Send batch every 400 ms or when job finishes
            if batch and (now - last_flush >= 0.4 or is_done):
                yield f"data: {json.dumps(batch)}\n\n"
                batch = []
                last_flush = now
                last_heartbeat = now

            # Keepalive comment every 15s to prevent proxy/browser timeout
            elif not batch and (now - last_heartbeat >= 15):
                yield ": keepalive\n\n"
                last_heartbeat = now

            if is_done:
                yield f"event: done\ndata: {status}\n\n"
                break

            time.sleep(0.1)

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


@app.route("/api/delete-posters", methods=["POST"])
def delete_posters():
    data = request.json or {}
    poster_type = data.get("type", "all")   # "movies", "series", or "all"

    settings   = load_settings()
    movies_dir = settings.get("pwPosterDownloader_movies_dir", "/fileserver/Filme")
    series_dir = settings.get("pwPosterDownloader_series_dir", "/fileserver/Serien")

    ARTWORK_NAMES = {
        "poster.jpg", "poster.png",
        "folder.jpg", "folder.png",
        "fanart.jpg",  "fanart.png",
        "backdrop.jpg","backdrop.png",
        "landscape.jpg","landscape.png",
        "banner.jpg",  "banner.png",
    }

    dirs_to_process = []
    if poster_type in ("movies", "all"):
        dirs_to_process.append(("movies", movies_dir))
    if poster_type in ("series", "all"):
        dirs_to_process.append(("series", series_dir))

    deleted = 0
    for kind, base_dir in dirs_to_process:
        try:
            for entry in os.scandir(base_dir):
                if not entry.is_dir():
                    continue
                for fname in os.listdir(entry.path):
                    if fname.lower() in ARTWORK_NAMES:
                        try:
                            os.remove(os.path.join(entry.path, fname))
                            deleted += 1
                        except OSError:
                            pass
        except Exception:
            pass

    # Trigger Plex library refresh
    section_ids = []
    if poster_type in ("movies", "all"):
        section_ids.append(1)
    if poster_type in ("series", "all"):
        section_ids.append(2)

    refreshed = True
    for sid in section_ids:
        try:
            http_requests.get(
                f"{PLEX_URL}/library/sections/{sid}/refresh",
                params={"X-Plex-Token": PLEX_TOKEN},
                timeout=10,
            )
        except Exception:
            refreshed = False

    return jsonify({"deleted": deleted, "refreshed": refreshed})


@app.route("/api/test-overlay", methods=["POST"])
def test_overlay():
    """Show current poster/thumb from Plex for a random item.
    No Kometa run — displays the actual overlay result from the last real run."""
    data = request.json or {}
    tool         = data.get("tool", "pwKometaManager")
    overlay_type = data.get("type", "movies")   # movies / series / episode
    is_episode   = overlay_type == "episode"

    cfg = SCRIPTS.get(tool)
    if not cfg:
        return jsonify({"error": "unknown tool"}), 404

    settings   = load_settings()
    series_dir = settings.get(f"{tool}_series_dir", "/fileserver/Serien")
    movies_dir = settings.get(f"{tool}_movies_dir", "/fileserver/Filme")

    if overlay_type == "movies":
        base_dir   = movies_dir
        section_id = 1
        tag_elem   = "Video"
    else:
        base_dir   = series_dir
        section_id = 2
        tag_elem   = "Directory"

    # 1. Pick a random folder
    try:
        entries = [e for e in os.scandir(base_dir) if e.is_dir()]
    except Exception as e:
        return jsonify({"error": f"Cannot list directory: {e}"}), 500
    if not entries:
        return jsonify({"error": "No entries found in directory"}), 404

    entry       = random.choice(entries)
    folder_name = entry.name
    match = re.match(r'^(.+?)\s*\(\d{4}\)', folder_name)
    title = match.group(1).strip() if match else re.sub(r'\s*\{[^}]+\}', '', folder_name).strip()

    # 2. Search Plex for the show/movie
    try:
        resp = http_requests.get(
            f"{PLEX_URL}/library/sections/{section_id}/all",
            params={"title": title, "X-Plex-Token": PLEX_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        item = root.find(f".//{tag_elem}")
    except Exception as e:
        return jsonify({"error": f"Plex search error: {e}"}), 500

    if item is None:
        return jsonify({"error": f"Item not found in Plex: '{title}'"}), 404

    show_key = item.get("ratingKey")

    # 3. For episode mode: pick a random episode
    if is_episode:
        try:
            ep_resp = http_requests.get(
                f"{PLEX_URL}/library/metadata/{show_key}/allLeaves",
                params={"X-Plex-Token": PLEX_TOKEN},
                timeout=15,
            )
            ep_resp.raise_for_status()
            ep_root  = ET.fromstring(ep_resp.content)
            episodes = ep_root.findall(".//Video")
            if not episodes:
                return jsonify({"error": "No episodes found"}), 404
            ep         = random.choice(episodes)
            target_key = ep.get("ratingKey")
            item_name  = f"{folder_name} – {ep.get('title', 'Episode')}"
        except Exception as e:
            return jsonify({"error": f"Episode fetch error: {e}"}), 500
    else:
        target_key = show_key
        item_name  = folder_name

    # 4. Fetch current poster/thumb from Plex (no Kometa run, no Plex modifications)
    poster_data = None
    try:
        thumb_resp = http_requests.get(
            f"{PLEX_URL}/library/metadata/{target_key}/thumb",
            params={"X-Plex-Token": PLEX_TOKEN},
            timeout=15,
        )
        if thumb_resp.ok:
            poster_data = thumb_resp.content
    except Exception:
        pass

    if not poster_data:
        return jsonify({"error": "Could not fetch image from Plex"}), 500

    response = Response(poster_data, mimetype="image/jpeg")
    response.headers["X-Item-Name"] = item_name
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
