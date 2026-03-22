#!/usr/bin/env python3
"""pwMediaManager – Web UI to run pwMediaEnhancer."""

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
    "pwPosterSync": {
        "label":       "pwPosterSync",
        "description": "Lädt die aktuell in Plex ausgewählten Poster herunter und speichert sie als poster.jpg im jeweiligen Ordner.",
        "readme_url":  "https://github.com/pennywise81/pwPosterSync#readme",
        "script":      "/scripts/pwPosterSync/pwPosterSync.py",
        "host_script": "/mnt/user/Fileserver/scripts/pwPosterSync/pwPosterSync.py",
        "log_prefix":  "pwPosterSync",
        "params": [
            {"id": "dry_run", "flag": "--dry-run", "label": "Dry-Run (nichts schreiben)"},
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
                "help":    "Pfad zum Ordner mit den Filmordnern (jeder Unterordner endet auf {imdb-ttXXX}).",
            },
            {
                "id":      "series_dir",
                "flag":    "--series-dir",
                "label":   "Serien-Verzeichnis",
                "default": "/fileserver/Serien",
                "help":    "Pfad zum Ordner mit den Serienordnern (jeder Unterordner endet auf {imdb-ttXXX}).",
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
        with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
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
    script = cfg["script"]
    cmd = ["python3", script] if script.endswith(".py") else ["bash", script]

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
                with open(log_path, encoding="utf-8", errors="replace") as f:
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


@app.route("/api/clear-logs", methods=["POST"])
def clear_logs():
    """Delete all log files except the newest one per tool."""
    deleted = 0
    for key, cfg in SCRIPTS.items():
        pattern = str(LOGS_DIR / f"{cfg['log_prefix']}_*.log")
        files = sorted(glob.glob(pattern), reverse=True)
        for f in files[1:]:   # keep files[0] (newest), delete the rest
            try:
                os.remove(f)
                deleted += 1
            except Exception:
                pass
    return jsonify({"ok": True, "deleted": deleted})


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
