import logging
import os
import re
from typing import Any, Dict, List
from fastapi import APIRouter, Body, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..services.player import Player
from ..utils.layout_store import send_active_layout
from ..services.playlists import (
    list_media, list_playlists, pl_load, pl_save, playlist_path,
    export_manufacturer
)

from ..config import LINE_NUMS, TEMPLATE_DIR


log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)


@router.post("/api/media/assign")
async def api_media_assign(req: dict):
    """
    JSON: {
      "files": ["a.jpg","b.mp4"],     # rel. zu /media
      "add_to": ["pl1","pl2"],        # optional
      "remove_from": ["pl3"],         # optional
      "defaults": { "mode":"fill","duration":0,"loop":1 }  # optional
    }
    """
    files = [f for f in (req.get("files") or []) if isinstance(f, str)]
    add_to = [p for p in (req.get("add_to") or []) if isinstance(p, str)]
    rem_from = [p for p in (req.get("remove_from") or []) if isinstance(p, str)]
    defaults = req.get("defaults") or {}
    def_mode = (defaults.get("mode") or "fill").lower()
    def_dur  = int(defaults.get("duration") or 0)
    def_loop = max(1, int(defaults.get("loop") or 1))

    changed = {"added": {}, "removed": {}}

    # hinzufügen
    for pl_name in add_to:
        try:
            pl = pl_load(pl_name) or {"name": pl_name, "mode": "repeat", "items": []}
            items = pl.get("items") or []
            have = { (it.get("file") or "").strip() for it in items }
            for f in files:
                if f not in have:
                    items.append({"file": f, "mode": def_mode, "duration": def_dur, "loop": def_loop})
            pl["items"] = items
            pl_save(pl_name, pl)
            changed["added"][pl_name] = len(files)
        except Exception as e:
            log.warning("assign add %s failed: %s", pl_name, e)

    # entfernen
    for pl_name in rem_from:
        try:
            pl = pl_load(pl_name)
            if not pl: 
                continue
            items = pl.get("items") or []
            files_set = set(files)
            new_items = [it for it in items if (it.get("file") or "") not in files_set]
            if len(new_items) != len(items):
                pl["items"] = new_items
                pl_save(pl_name, pl)
                changed["removed"][pl_name] = len(items) - len(new_items)
        except Exception as e:
            log.warning("assign remove %s failed: %s", pl_name, e)

    return {"ok": True, "changed": changed}


@router.get("/api/playlists")
def api_playlists_names():
    return {"playlists": list_playlists()}

@router.get("/api/playlist/{pl_name}")
def api_playlist_get(pl_name: str):
    pl = pl_load(pl_name) or {"name": pl_name, "mode": "repeat", "items": []}
    return pl

@router.post("/api/playlist/{pl_name}/bulk")
def api_playlist_bulk(pl_name: str, payload: Dict[str, Any] = Body(...)):
    action = (payload.get("action") or "").lower()
    files: List[str] = [f for f in (payload.get("files") or []) if isinstance(f, str)]
    defaults: Dict[str, Any] = dict(payload.get("defaults") or {})

    if action not in ("add", "remove", "set"):
        return JSONResponse({"error": "action must be add/remove/set"}, status_code=400)
    if not files:
        return {"ok": True, "changed": 0}

    pl = pl_load(pl_name) or {"name": pl_name, "mode": "repeat", "items": []}
    items: List[Dict[str, Any]] = list(pl.get("items") or [])
    file_set = set(files)
    changed = 0

    if action == "add":
        have = {(it.get("file") or "").strip() for it in items}
        for fn in files:
            if fn in have:
                continue
            items.append({
                "file": fn,
                "mode": defaults.get("mode", "fill"),
                "loop": int(defaults.get("loop", 1) or 1),
                "duration": int(defaults.get("duration", 0) or 0),
                "start": defaults.get("start", ""),
                "end": defaults.get("end", ""),
            })
            changed += 1
    elif action == "set":
        by_file = {}
        for it in items:
            fn_prev = (it.get("file") or "").strip()
            if not fn_prev:
                continue
            by_file[fn_prev] = {
                "mode": it.get("mode", "fill"),
                "loop": int(it.get("loop", 1) or 1),
                "duration": int(it.get("duration", 0) or 0),
                "start": it.get("start", ""),
                "end": it.get("end", ""),
            }

        seen = set()
        ordered_files = []
        for fn in files or []:
            fn = (fn or "").strip()
            if not fn or fn in seen:
                continue
            seen.add(fn)
            ordered_files.append(fn)

        new_items = []
        for fn in ordered_files:
            meta = by_file.get(fn, None)
            if meta is None:
                meta = {
                    "mode": defaults.get("mode", "fill"),
                    "loop": int(defaults.get("loop", 1) or 1),
                    "duration": int(defaults.get("duration", 0) or 0),
                    "start": defaults.get("start", ""),
                    "end": defaults.get("end", ""),
                }
            new_items.append({"file": fn, **meta})

        pl["items"] = new_items
        pl_save(pl_name, pl)
        return {"ok": True, "count": len(pl["items"])}
    else: 
        before = len(items)
        items = [it for it in items if (it.get("file") or "") not in file_set]
        changed = before - len(items)

    pl["items"] = items
    pl_save(pl_name, pl)  

    return {"ok": True, "changed": changed, "count": len(items)}

@router.post("/playlist/{name}/start")
def playlist_start(request: Request, name: str):
    app = request.app
    send_active_layout(app, line_nums=LINE_NUMS)

    if not app.state.PLAYER.is_alive():
        app.state.PLAYER = Player(app.state.LED)
        app.state.PLAYER.start()

    if getattr(app.state, "MQTT", None):
        def _np(title, playlist, file_or_token):
            try:
                app.state.MQTT.publish_now_playing(title=title, playlist=playlist, file_or_token=file_or_token)
            except Exception:
                pass
        app.state.PLAYER.on_now_playing = _np

    # Playlist laden
    pl = pl_load(name)
    app.state.PLAYER.load(pl)
    log.info(f"[PLAY] Playlist: {name} gestartet")
    return RedirectResponse("/", status_code=303)

@router.post("/playlist/{name}/stop")
def playlist_stop(request: Request, name:str):
    app = request.app
    app.state.PLAYER.stop_playlist()
    app.state.LED.clear((0,0,0))

    if getattr(app.state, "MQTT", None):
        try:
            app.state.MQTT.publish_now_playing(title=None, playlist=None, file_or_token=None)
        except Exception:
            pass

    return RedirectResponse("/", status_code=303)

@router.post("/playlist/{name}/delete")
def playlist_delete(name:str):
    try: os.remove(playlist_path(name))
    except: pass
    return RedirectResponse("/", status_code=303)

@router.get("/playlist/{name}/export", response_class=PlainTextResponse)
def playlist_export(name:str):
    txt = export_manufacturer(pl_load(name))
    return PlainTextResponse(txt, media_type="text/plain; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{name}.txt"'
    })

@router.post("/playlist/create")
def playlist_create(name: str = Form(...)):
    name = re.sub(r"[^\w\-\.]", "_", name.strip())
    pl_save(name, {"name": name, "items": []})
    return RedirectResponse(f"/playlist/{name}/edit", status_code=303)

@router.get("/playlist/{name}/edit", response_class=HTMLResponse)
def playlist_edit(request: Request, name: str):
    pl = pl_load(name)
    return templates.TemplateResponse("playlist_edit_v2.html", {
        "request": request,
        "name": name,
        "items": pl.get("items", []),
        "media": list_media(),
        "pl": pl
    })

@router.post("/playlist/{name}/save")
async def playlist_save(request: Request, name: str):
    """
    Speichert die Playlist. Antwort-Modus:
      - Redirect 303 auf "/" 
      - JSON {"status":"ok","count":N} wenn
          * ?json=1|true|yes ODER
          * Accept: application/json ODER
          * X-Requested-With: XMLHttpRequest
    Body-Formate:
      - multipart/x-www-form-urlencoded (bestehende Formularfelder file[]/mode[]/...)
      - application/json  { "mode": "...", "items": [ {file,mode,loop,duration,start,end} ] }
    """
    def want_json_resp(req: Request) -> bool:
        qp = req.query_params.get("json", "").lower()
        if qp in ("1", "true", "yes"): 
            return True
        acc = (req.headers.get("accept") or "").lower()
        if "application/json" in acc: 
            return True
        if (req.headers.get("x-requested-with") or "").lower() == "xmlhttprequest":
            return True
        return False

    # --- Request einlesen ---
    items = []
    pl_mode = "repeat"

    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        payload = await request.json()
        pl_mode = (payload.get("mode") or "repeat").lower()
        if pl_mode not in ("repeat", "random", "once"):
            pl_mode = "repeat"
        for it in (payload.get("items") or []):
            f = (it.get("file") or "").strip()
            if not f:
                continue
            items.append({
                "file": f,
                "mode": (it.get("mode") or "fill"),
                "loop": int(it.get("loop") or 1),
                "duration": int(it.get("duration") or 0),
                "start": it.get("start") or "",
                "end": it.get("end") or "",
            })
    else:
        form = await request.form()
        files     = form.getlist("file[]")
        modes     = form.getlist("mode[]")
        loops     = form.getlist("loop[]")
        durations = form.getlist("duration[]")
        starts    = form.getlist("start[]")
        ends      = form.getlist("end[]")
        pl_mode   = (form.get("mode") or "repeat").lower()
        if pl_mode not in ("repeat","random","once"):
            pl_mode = "repeat"

        for i, f in enumerate(files):
            if not f:
                continue
            items.append({
                "file": f,
                "mode": (modes[i] if i < len(modes) else "fill") or "fill",
                "loop": int(loops[i]) if i < len(loops) and (loops[i] or "").isdigit() else 1,
                "duration": int(durations[i]) if i < len(durations) and (durations[i] or "").isdigit() else 0,
                "start": starts[i] if i < len(starts) else "",
                "end":   ends[i]   if i < len(ends)   else "",
            })

    # --- Speichern ---
    pl = pl_load(name) or {"name": name}
    pl["items"] = items
    pl["mode"]  = pl_mode
    pl_save(name, pl)

    # --- Antwort ---
    if want_json_resp(request):
        return JSONResponse({"status": "ok", "count": len(items), "mode": pl_mode})
    return RedirectResponse("/", status_code=303)
