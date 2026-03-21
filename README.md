# pwMediaManager

Web-Oberfläche zur Steuerung von **pwMediaEnhancer** und **pwPosterDownloader** auf einem Unraid-Server.

## Features

- **Tabellarische Übersicht** beider Tools mit Beschreibung und konfigurierbaren Parametern
- **Live-Streaming** der Skript-Ausgabe direkt im Browser (Server-Sent Events)
- **Log-Archiv**: Alle bisherigen Läufe sind als Links in der Tabelle abrufbar und öffnen sich in einem neuen Tab
- **Dark-Theme UI** – optimiert für den täglichen Gebrauch
- Läuft als **Docker-Container** mit minimalem Overhead

## Screenshots

```
┌────────────────────────────────────────────────────────────────────┐
│  ⚙️  pwMediaManager                                                │
│  Steuerung für pwMediaEnhancer & pwPosterDownloader                │
├─────────────────────┬──────────────────┬────────────────┬──────────┤
│ Tool                │ Beschreibung     │ Parameter      │ Logs     │
├─────────────────────┼──────────────────┼────────────────┼──────────┤
│ pwMediaEnhancer ⏳  │ Sortiert Filme … │ ☐ Dry-Run     │ 📄 …log  │
│                     │                  │ /fileserver    │ 📄 …log  │
├─────────────────────┼──────────────────┼────────────────┼──────────┤
│ pwPosterDownloader  │ Lädt Poster …   │ ☐ Dry-Run     │ 📄 …log  │
│                     │                  │ ☐ Nur Filme   │          │
│                     │                  │ ☐ Nur Serien  │          │
│                     │                  │ ☐ Skip exist. │          │
└─────────────────────┴──────────────────┴────────────────┴──────────┘

Live-Ausgabe – pwMediaEnhancer (pwMediaEnhancer_20260321_120000.log)
┌────────────────────────────────────────────────────────────────────┐
│ 🔍 DRY-RUN – no files will be changed                              │
│ 📽️  Movies → /fileserver/Filme                                    │
│   🎬 'Inception (2010)' → 'Inception (2010)'                       │
│   …                                                                │
└────────────────────────────────────────────────────────────────────┘
```

## Voraussetzungen

- Docker + Docker Compose auf dem Zielsystem (Unraid)
- `pwMediaEnhancer` und `pwPosterDownloader` als geklonte Repos auf dem Fileserver
- `/boot/config/pwMediaEnhancer.conf` mit API-Keys und Plex-Konfiguration

## Installation

```bash
# Repo klonen (auf dem Mac / Entwicklungsrechner)
git clone https://github.com/pennywise81/pwMediaManager.git
cd pwMediaManager

# Auf Unraid kopieren und deployen
scp -r . pwu:/mnt/user/appdata/pwMediaManager/
ssh pwu "cd /mnt/user/appdata/pwMediaManager && docker compose up -d --build"
```

## Konfiguration

Die Konfiguration erfolgt ausschließlich über Volumes in `docker-compose.yml`.

| Volume (Host)                                       | Container-Pfad                        | Beschreibung                       |
|-----------------------------------------------------|---------------------------------------|------------------------------------|
| `/mnt/user/Fileserver/scripts/pwMediaEnhancer`      | `/scripts/pwMediaEnhancer` (ro)       | pwMediaEnhancer-Skript             |
| `/mnt/user/Fileserver/scripts/pwPosterDownloader`   | `/scripts/pwPosterDownloader` (ro)    | pwPosterDownloader-Skript          |
| `/boot/config/pwMediaEnhancer.conf`                 | `/boot/config/pwMediaEnhancer.conf` (ro) | API-Keys, Plex-Konfiguration    |
| `/mnt/user/Fileserver`                              | `/fileserver`                         | Medienverzeichnis (Schreibzugriff) |
| `/mnt/user/appdata/pwMediaManager/logs`             | `/logs`                               | Log-Dateien aller Läufe            |

## Parameter

### pwMediaEnhancer
| Parameter      | Beschreibung                                  |
|----------------|-----------------------------------------------|
| `--dry-run`    | Keine Dateiänderungen, nur Vorschau ausgeben  |

### pwPosterDownloader
| Parameter           | Beschreibung                                         |
|---------------------|------------------------------------------------------|
| `--dry-run`         | Kein Download, kein Plex-Upload                      |
| `--movies-only`     | Nur Filmbibliothek verarbeiten                       |
| `--series-only`     | Nur Serienbibliothek verarbeiten                     |
| `--skip-existing`   | Ordner mit vorhandenem `folder.jpg` überspringen     |
| `--plex-upload-only`| Nur vorhandene Poster zu Plex hochladen, kein Download |

## Update

```bash
ssh pwu "cd /mnt/user/appdata/pwMediaManager && git pull && docker compose up -d --build"
```

## Port

Web-UI erreichbar unter: `http://<unraid-ip>:8082`

## Architektur

```
Browser
  │
  ▼
pwMediaManager (Flask, Port 8080)
  ├── GET  /           → Dashboard (Tabelle)
  ├── POST /run/<tool> → Skript starten, Job-ID zurückgeben
  ├── GET  /stream/<id>→ SSE: Live-Ausgabe streamen
  ├── GET  /status/<id>→ Job-Status abfragen
  └── GET  /logs/<file>→ Log-Datei anzeigen (öffnet in neuem Tab)

Volumes
  ├── /scripts/pwMediaEnhancer    ← git repo (ro)
  ├── /scripts/pwPosterDownloader ← git repo (ro)
  ├── /boot/config/pwMediaEnhancer.conf (ro)
  ├── /fileserver                 ← Mediendateien (rw)
  └── /logs                       ← Log-Archiv (rw)
```
