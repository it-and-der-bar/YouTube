# Screeny — README

## Was ist das?
Screeny ist ein kleines webbasiertes Interface um nuvoLED Panele zu betreiben und bietet dazu eine Weboberfläche zum Verwalten von Medien (Bilder, Videos, Texte), Playlists, Zeitplänen und zur Ansteuerung von LED‑Layouts. Die App liefert außerdem eine MQTT‑Brücke (Home Assistant Discovery), Tasmota‑Integration für Smart‑Plugs/Relays und einfache Scheduler‑Funktionen.

**Technologien:** FastAPI, Uvicorn, Jinja2, Pillow, OpenCV, NumPy, paho‑mqtt.

## Ausschluss
Die Software kommt wie sie ist, es wird keine Gewährleistung, Garantie oder Haftung für Schäden übernommen.
Benutztung auf eigene Gefahr!

---

## Quickstart (lokal, Entwicklung)

1. Python‑Umgebung anlegen:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

2. App starten (Standard läuft auf `0.0.0.0:8000`):
```bash
python app.py
# oder
uvicorn app:app --host 0.0.0.0 --port 8000
```

Öffne danach `http://localhost:8000` im Browser.

### Startparameter
Die App liest ein optionales Startargument um das Panelinterface zu definieren `--bind-ip` (siehe `screeny/config.py`), z.B.:
```bash
python app.py --bind-ip 192.168.1.50
```

---

## Docker 

### Dockerfile
Eine passende `Dockerfile` liegt im Projekt bei (siehe `./Dockerfile`). Sie installiert systemabhängige Bibliotheken (für OpenCV / Pillow) und die Python‑Requirements.

### Build:
```bash
docker build -t screeny:latest -f Dockerfile .
```

### WICHTIG — Netzmodus `host`
Die App verschickt UDP‑Broadcasts (z.B. zur Erkennung / Steuerung von Panels). Damit das zuverlässig funktioniert, **muss** der Container im Host‑Netzwerk laufen:

```bash
# Beispiel (Linux):
docker run -d --name screeny --network host \
  -v "$(pwd)/configs:/app/configs" \
  -v "$(pwd)/media:/app/media" \
  -v "$(pwd)/playlists:/app/playlists" \
  -v "$(pwd)/logs:/app/logs" \
  screeny:latest
```

> Hinweis: `--network host` funktioniert **nur** auf Linux‑Hosts mit Docker‑Engine. Docker Desktop (Mac/Windows) unterstützt host‑networking nicht wie unter Linux — in diesem Fall funktionieren UDP‑Broadcasts evtl. nicht zuverlässig.

Du kannst beim `docker run` auch Startparameter anhängen, z.B.:
```bash
docker run --rm --network host screeny:latest --bind-ip 192.168.1.50
```

### Docker Compose (Beispiel)
```yaml
version: "3.8"
services:
  screeny:
    build: .
    image: screeny:latest
    network_mode: "host"
    volumes:
      - ./configs:/app/configs
      - ./media:/app/media
      - ./playlists:/app/playlists
      - ./logs:/app/logs
    restart: unless-stopped
```

---

## Konfiguration (`configs/config.json`)
Die App liest die Konfiguration aus `configs/config.json`. Falls die Datei nicht existiert, wird ein Default angelegt. Anbei eine Erklärung, ein manuelles anlegen ist nicht nötig!

```json
{
  "autostart": "",
  "line_num": 0,
  "mqtt": {
    "enabled": false,
    "host": "mqtt.local",
    "port": 1883,
    "user": "",
    "password": ""
  },
  "tasmota": {
    "enabled": false,
    "host": "192.168.1.100",
    "user": "",
    "password": "",
    "timeout": 5
  },
  "power_control": true,
  "auto_off_min": 10
}
```

**Wichtige Keys (Kurz):**
- `autostart` — Name einer Playlist, die automatisch gestartet wird.  
- `mqtt` — MQTT‑Broker‑Daten (wenn `enabled: true` wird MQTT gestartet).  
- `tasmota` — Host/credentials für Tasmota‑Geräte (Power / Energy API).  
- `auto_off_min` — Minuten bis automatischem Abschalten (Scheduler).

---

## Ordnerstruktur (wichtig für Volumes)
- `configs/` → `config.json`, `panel_layout.json` (persistent mounten!)  
- `media/` → Mediendateien (Bilder/Videos)  
- `playlists/` → Playlists als JSON  
- `logs/` → Logdateien

Mounte diese Verzeichnisse in den Container, um Inhalte persistent zu halten.

---
## Sonstiges / Hinweise
- Das Repo enthält ein `setup.sh` damit kann screeny als Dienst installiert werden (für lokale Linux‑Installationen)
- Die App startet standardmäßig auf Port `8000`

## Lizenz
Der Code darf nicht kommerziell weiterverwendet werden.
PolyForm Noncommercial
---