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
    from PIL import Image, ImageDraw, ImageFont
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

KOMETA_CONFIG_DIR   = os.environ.get("KOMETA_CONFIG_DIR",   "/mnt/user/appdata/kometa/config")
KOMETA_CONFIG_LOCAL = Path(os.environ.get("KOMETA_CONFIG_LOCAL", "/kometa-config"))
KOMETA_ASSETS       = Path(os.environ.get("KOMETA_ASSETS", "/kometa-assets"))

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


@app.route("/api/delete-posters", methods=["POST"])
def delete_posters():
    data = request.json or {}
    poster_type = data.get("type", "all")   # "movies", "series", or "all"

    settings   = load_settings()
    movies_dir = settings.get("pwPosterDownloader_movies_dir", "/fileserver/Filme")
    series_dir = settings.get("pwPosterDownloader_series_dir", "/fileserver/Serien")

    ARTWORK_NAMES = {
        "poster.jpg", "poster.png", "folder.jpg", "folder.png",
        "fanart.jpg", "fanart.png", "backdrop.jpg", "backdrop.png",
        "landscape.jpg", "landscape.png", "banner.jpg", "banner.png",
    }

    # 1. Delete local artwork files from disk
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

    # 2. For each Plex item: select the first metadata-agent poster (tmdb/tvdb/etc.)
    PREFERRED_PROVIDERS = {"tmdb", "tvdb", "imdb", "fanarttv", "gracenote"}
    section_map = {}
    if poster_type in ("movies", "all"):
        section_map[1] = "Video"
    if poster_type in ("series", "all"):
        section_map[2] = "Directory"

    reset = 0
    errors = 0
    for sid, elem_tag in section_map.items():
        try:
            resp = http_requests.get(
                f"{PLEX_URL}/library/sections/{sid}/all",
                params={"X-Plex-Token": PLEX_TOKEN},
                timeout=30,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = root.findall(f".//{elem_tag}")
        except Exception:
            continue

        for item in items:
            rk = item.get("ratingKey")
            if not rk:
                continue
            try:
                pr = http_requests.get(
                    f"{PLEX_URL}/library/metadata/{rk}/posters",
                    params={"X-Plex-Token": PLEX_TOKEN},
                    timeout=10,
                )
                if not pr.ok:
                    continue
                pr_root = ET.fromstring(pr.content)
                # Pick first poster from a metadata provider (not upload://)
                chosen_url = None
                for photo in pr_root.findall(".//Photo"):
                    provider = photo.get("provider") or ""
                    rk_url   = photo.get("ratingKey", "")
                    if provider in PREFERRED_PROVIDERS or (
                        provider == "" and not rk_url.startswith("upload://")
                    ):
                        chosen_url = rk_url
                        break
                if chosen_url:
                    http_requests.put(
                        f"{PLEX_URL}/library/metadata/{rk}/poster",
                        params={"url": chosen_url, "X-Plex-Token": PLEX_TOKEN},
                        timeout=10,
                    )
                    reset += 1
            except Exception:
                errors += 1

    return jsonify({"deleted": deleted, "reset": reset, "errors": errors})


def _plex_media_info(rk: str) -> dict:
    """Return resolution key and audio codec key for a Plex item."""
    try:
        resp = http_requests.get(
            f"{PLEX_URL}/library/metadata/{rk}",
            params={"X-Plex-Token": PLEX_TOKEN, "includeStreams": 1},
            timeout=10,
        )
        if not resp.ok:
            return {}
        root  = ET.fromstring(resp.content)
        video = root.find(".//Stream[@streamType='1']")
        audio = root.find(".//Stream[@streamType='2']")

        resolution = "1080p"
        if video:
            h    = int(video.get("height", 0))
            hdr  = video.get("colorTrc", "").lower() in ("smpte2084", "arib-std-b67", "bt2020")
            resolution = ("4khdr" if hdr else "4k") if h >= 2160 else \
                         ("1080phdr" if hdr else "1080p") if h >= 1080 else \
                         "720p" if h >= 720 else "480p"

        audio_codec = None
        if audio:
            dt = audio.get("displayTitle", "").lower()
            co = audio.get("codec",        "").lower()
            if   "truehd" in dt and "atmos" in dt: audio_codec = "truehd_atmos"
            elif "truehd" in dt:                   audio_codec = "truehd"
            elif "atmos"  in dt:                   audio_codec = "atmos"
            elif "dts-x"  in dt or "dtsx" in dt:   audio_codec = "dtsx"
            elif "dts-ma" in dt:                   audio_codec = "ma"
            elif "dts"    in dt:                   audio_codec = "dts"
            elif "dd+"    in dt or "eac3" in co:   audio_codec = "plus"
            elif "dolby"  in dt:                   audio_codec = "digital"
            elif "aac"    in co:                   audio_codec = "aac"
            elif "flac"   in co:                   audio_codec = "flac"
            elif "mp3"    in co:                   audio_codec = "mp3"
        return {"resolution": resolution, "audio_codec": audio_codec}
    except Exception:
        return {}


def _composite_kometa_overlays(poster_bytes: bytes, rating: str | None, rk: str) -> bytes:
    """Composite Kometa's real badge PNGs onto a poster using PIL.
    Uses the exact same badge images that kometateam/kometa would apply."""
    if not PIL_AVAILABLE or not KOMETA_ASSETS.exists():
        return poster_bytes
    try:
        img = Image.open(io.BytesIO(poster_bytes)).convert("RGBA")
        w, h = img.size
        scale = w / 680.0   # Kometa designs for ~680px wide posters

        info = _plex_media_info(rk)

        def load_badge(rel_path: str) -> Image.Image | None:
            p = KOMETA_ASSETS / rel_path
            return Image.open(p).convert("RGBA") if p.exists() else None

        def paste_scaled(badge: Image.Image, x: int, y: int) -> None:
            bw = int(badge.width  * scale)
            bh = int(badge.height * scale)
            img.paste(badge.resize((bw, bh), Image.LANCZOS), (x, y), badge.resize((bw, bh), Image.LANCZOS))

        off = max(10, int(15 * scale))

        # 1. Resolution badge — top-left
        res_key = info.get("resolution", "1080p")
        badge   = load_badge(f"resolution/{res_key}.png") or load_badge("resolution/1080p.png")
        if badge:
            paste_scaled(badge, off, off)

        # 2. Audio codec badge — top-center
        ac_key = info.get("audio_codec")
        if ac_key:
            badge = load_badge(f"audio_codec/{ac_key}.png")
            if badge:
                bw = int(badge.width * scale)
                bh = int(badge.height * scale)
                badge_r = badge.resize((bw, bh), Image.LANCZOS)
                img.paste(badge_r, ((w - bw) // 2, off), badge_r)

        # 3. IMDb rating badge — bottom-right
        #    Layout: [IMDb_logo] [dark_pill_with_number]
        imdb_src  = load_badge("rating/IMDb.png")
        font_path = KOMETA_ASSETS / "Inter-Bold.ttf"
        if imdb_src and rating:
            rating_text = f"{float(rating):.1f}"
            font_size = max(20, int(63 * scale))
            try:
                font = ImageFont.truetype(str(font_path), font_size) \
                    if font_path.exists() else ImageFont.load_default(size=font_size)
            except Exception:
                font = ImageFont.load_default(size=font_size)

            d    = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            tb   = d.textbbox((0, 0), rating_text, font=font)
            tw   = tb[2] - tb[0]
            th   = tb[3] - tb[1]

            badge_h = max(int(75 * scale), th + int(20 * scale))
            ratio   = badge_h / 75
            logo    = imdb_src.resize((int(149 * ratio), badge_h), Image.LANCZOS)

            pad   = max(10, int(15 * scale))
            gap   = max(4,  int(8  * scale))
            bg_w  = tw + pad * 2
            total = logo.width + gap + bg_w

            badge = Image.new("RGBA", (total, badge_h), (0, 0, 0, 0))
            badge.paste(logo, (0, 0), logo)

            bd = ImageDraw.Draw(badge)
            rx = logo.width + gap
            bd.rounded_rectangle(
                [rx, 0, rx + bg_w, badge_h],
                radius=max(6, int(30 * ratio * 0.6)),
                fill=(0, 0, 0, 153),
            )
            bd.text((rx + pad, (badge_h - th) // 2 - tb[1]),
                    rating_text, font=font, fill=(255, 255, 255, 255))

            std_off = max(15, int(30 * scale))
            img.paste(badge, (w - total - std_off, h - badge_h - std_off), badge)

        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=95)
        return out.getvalue()
    except Exception:
        return poster_bytes


@app.route("/api/test-overlay", methods=["POST"])
def test_overlay():
    """Fetch a random poster from Plex and composite Kometa's real badge PNGs onto it.
    Uses the actual badge images from the kometateam/kometa container — no Kometa run,
    no side-effects on the Plex library."""
    data = request.json or {}
    tool         = data.get("tool", "pwKometaManager")
    overlay_type = data.get("type", "movies")
    is_episode   = overlay_type == "episode"

    if not SCRIPTS.get(tool):
        return jsonify({"error": "unknown tool"}), 404

    section_id = 2 if is_episode else 1
    tag_elem   = "Directory" if is_episode else "Video"

    # 1. Pick a random item from Plex
    try:
        resp = http_requests.get(
            f"{PLEX_URL}/library/sections/{section_id}/all",
            params={"X-Plex-Token": PLEX_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        items = ET.fromstring(resp.content).findall(f".//{tag_elem}")
        if not items:
            return jsonify({"error": "No items found"}), 404
        item = random.choice(items)
    except Exception as e:
        return jsonify({"error": f"Plex error: {e}"}), 500

    plex_title = item.get("title", "")
    show_key   = item.get("ratingKey")
    rating     = item.get("rating")

    # 2. For episode mode: pick a random episode
    if is_episode:
        try:
            ep_resp = http_requests.get(
                f"{PLEX_URL}/library/metadata/{show_key}/allLeaves",
                params={"X-Plex-Token": PLEX_TOKEN},
                timeout=15,
            )
            ep_resp.raise_for_status()
            episodes = ET.fromstring(ep_resp.content).findall(".//Video")
            if not episodes:
                return jsonify({"error": "No episodes found"}), 404
            ep         = random.choice(episodes)
            target_key = ep.get("ratingKey")
            ep_title   = ep.get("title", "Episode")
            item_name  = f"{plex_title} - {ep_title}".encode("ascii", "replace").decode("ascii")
        except Exception as e:
            return jsonify({"error": f"Episode fetch error: {e}"}), 500
    else:
        target_key = show_key
        item_name  = plex_title.encode("ascii", "replace").decode("ascii")

    # 3. Fetch poster/thumb
    try:
        thumb_resp = http_requests.get(
            f"{PLEX_URL}/library/metadata/{target_key}/thumb",
            params={"X-Plex-Token": PLEX_TOKEN},
            timeout=15,
        )
        if not thumb_resp.ok:
            return jsonify({"error": "Could not fetch image from Plex"}), 500
        poster_data = thumb_resp.content
    except Exception as e:
        return jsonify({"error": f"Thumb fetch error: {e}"}), 500

    # 4. Composite Kometa's real badge PNGs onto the poster
    poster_data = _composite_kometa_overlays(poster_data, rating, target_key)

    response = Response(poster_data, mimetype="image/jpeg")
    response.headers["X-Item-Name"] = item_name
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
