#!/usr/bin/env python3
"""pwMediaManager – Web UI to run pwMediaEnhancer and pwPosterDownloader."""

import os
import subprocess
import threading
import uuid
import json
import time
import glob
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, Response, jsonify, send_file, abort

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

SCRIPTS = {
    "pwMediaEnhancer": {
        "label":       "pwMediaEnhancer",
        "description": "Sortiert und benennt Filme & Serien nach Plex-Standard um.",
        "readme_url":  "https://github.com/pennywise81/pwMediaEnhancer#readme",
        "script":      "/scripts/pwMediaEnhancer/pwMediaEnhancer.sh",
        "log_prefix":  "pwMediaEnhancer",
        "params": [
            {"id": "dry_run",  "flag": "--dry-run",  "label": "Dry-Run (keine Änderungen)"},
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
