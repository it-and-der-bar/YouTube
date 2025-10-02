import os, json, logging, datetime
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..services.playlists import list_playlists, pl_save
from ..services.nlh_importer import list_candidate_roots, scan_playlists_in, import_manufacturer_text
from ..config import LINE_NUMS, TEMPLATE_DIR, MEDIA_DIR, CONFIG_FILE


log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


def _save_cfg(app):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(app.state.cfg, f, ensure_ascii=False, indent=2)
        log.info("config saved: %s", app.state.cfg)
    except Exception as e:
        log.exception("config save error: %s", e)

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    cfg = getattr(request.app.state, "cfg", {}) or {}
    cfg.setdefault("mqtt", {})
    cfg.setdefault("tasmota", {})
    pls = list_playlists()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "cfg": cfg,
        "playlists": pls,
    })

@router.post("/settings/autostart")
def settings_autostart(request: Request, plname: str = Form("")):
    app = request.app
    app.state.cfg["autostart"] = (plname or "").strip()
    _save_cfg(app)
    return RedirectResponse("/settings", status_code=303)

@router.post("/settings/save")
async def settings_save(request: Request):
    form = await request.form()
    app = request.app

    # MQTT
    mqtt_cfg = {
        "enabled": bool(form.get("mqtt_enabled")),
        "host": (form.get("mqtt_host") or "").strip(),
        "port": int(form.get("mqtt_port") or 1883),
        "user": (form.get("mqtt_user") or "").strip(),
        "password": (form.get("mqtt_password") or "").strip(),
        "base": (form.get("mqtt_base") or "screeny").strip() or "screeny",
    }

    # Tasmota
    tasmota_cfg = {
        "enabled": bool(form.get("tasmota_enabled")),
        "power_control": bool(form.get("tasmota_power_control")),
        "host": (form.get("tasmota_host") or "").strip(),
        "user": (form.get("tasmota_user") or "").strip(),
        "password": (form.get("tasmota_password") or "").strip(),
        "timeout": int(form.get("tasmota_timeout") or 5),
        "power_wait_s": int(form.get("tasmota_power_wait_s") or 10),
        "auto_off_min": int(form.get("tasmota_auto_off_min") or 10),
    }

    app.state.cfg.setdefault("mqtt", {}).update(mqtt_cfg)
    app.state.cfg.setdefault("tasmota", {}).update(tasmota_cfg)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(app.state.cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        log.exception("config save error")

    if getattr(app.state, "MQTT", None):
        try:
            from ..services.playlists import list_playlists
            pls = list_playlists()
            app.state.MQTT.announce_discovery(pls, thumb_url=None)
        except Exception:
            pass

    return RedirectResponse("/settings", status_code=303)

@router.get("/panel-config", response_class=HTMLResponse)
def panel_config_alias(request: Request):
    return templates.TemplateResponse("panel_config.html", {"request": request})


@router.post("/autostart")
def route_autostart(request: Request, plname: str = Form("")):
    app = request.app
    app.state.cfg["autostart"] = plname.strip()
    _save_cfg(app)
    return RedirectResponse("/", status_code=303)

@router.get("/api/importer/roots")
async def importer_roots():
    """Laufwerke / Mounts für das Dropdown."""
    roots = list_candidate_roots()
    return {"roots": roots}

@router.get("/api/importer/scan")
async def importer_scan(root: str = Query(..., description="Root/Mount zum Scannen"),
                        recursive: bool = True):
    """Nur im gewählten Root nach Hersteller-Playlisten suchen."""
    files = scan_playlists_in(root, recursive=recursive)
    return {"root": root, "files": files}

@router.post("/api/importer/import")
async def importer_import(payload: dict):
    """
    Datei importieren und als Playlist anlegen.
    Erwartet: { "path": "/mnt/share/xyz.txt", "name": "Meine Herstellerliste" }
    """
    path = (payload or {}).get("path", "")
    name = (payload or {}).get("name", "") or os.path.splitext(os.path.basename(path))[0]
    if not path or not os.path.isfile(path):
        return {"ok": False, "error": "Datei nicht gefunden"}

    txt = open(path, "r", encoding="utf-8", errors="ignore").read()
    pl = import_manufacturer_text(txt, base_dir=os.path.dirname(path))
    pl["name"] = name or f"Imported {datetime.datetime.now():%Y-%m-%d %H:%M}"

    try:
        from .services.playlists import save_playlist  # type: ignore
        save_playlist(pl)
        return {"ok": True, "created": pl.get("name"), "items": len(pl.get("items", []))}
    except Exception:
        pass

    try:
        from .config import MEDIA_DIR
        playlists_dir = os.path.join(os.path.dirname(MEDIA_DIR), "playlists")
        os.makedirs(playlists_dir, exist_ok=True)
        out = os.path.join(playlists_dir, f"{pl['name']}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(pl, f, ensure_ascii=False, indent=2)
        return {"ok": True, "created": pl.get("name"), "items": len(pl.get("items", []))}
    except Exception as e:
        return {"ok": False, "error": f"Speichern fehlgeschlagen: {e}"}
