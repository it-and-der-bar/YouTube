import os, json, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from middleware.http_error_mirror import build_http_error_mirror
from .services.playlists import list_playlists
from .services.mqtt_bridge import ScreenyMqtt
from .utils.layout_store import send_active_layout

from .config import BASE_DIR, CONFIG_FILE, LINE_NUMS, MEDIA_DIR, PLAYLIST_DIR, TEMPLATE_DIR, STATIC_DIR
from .logging_config import _attach_uvicorn_file_handlers, configure_logging
from .services.led import LedBroadcaster
from .services.player import Player
from .services.scheduler import TasmotaScheduler

from .routes.web import router as web_router
from .routes.thumbs import router as thumbs_router
from .routes.schedule import router as schedule_router
from .routes.tasmota import router as tasmota_router
from .routes.player import router as player_router
from .routes.media import router as media_router
from .routes.playlist import router as playlist_router
from .routes.config import router as config_router
from .routes.panels import router as panels_router


import logging

import threading, time
from .services.tasmota import get_power, set_power


configure_logging("INFO")
log = logging.getLogger(__name__)

def cfg_load():
    if os.path.exists(CONFIG_FILE):
        try:
            return json.load(open(CONFIG_FILE, "r", encoding="utf-8"))
        except Exception:
            pass
    return {"autostart": "", "line_num": 0}

def cfg_save(d):
    json.dump(d, open(CONFIG_FILE, "w", encoding="utf-8"), indent=2)


def _tasmota_power_wait_s(app) -> int:
    tcfg = (app.state.cfg or {}).get("tasmota") or {}
    try: return max(0, int(tcfg.get("power_wait_s", 10)))
    except Exception: return 10

def _tasmota_auto_off_min(app) -> int:
    tcfg = (app.state.cfg or {}).get("tasmota") or {}
    try: return max(0, int(tcfg.get("auto_off_min", 10)))
    except Exception: return 10

LED = LedBroadcaster()
PLAYER = Player(LED)
SCHED = TasmotaScheduler() if TasmotaScheduler else None

def _count_media() -> int:
    try:
        total = 0
        for root, _, files in os.walk(MEDIA_DIR):
            total += len([f for f in files if not f.startswith(".")])
        return total
    except Exception:
        return 0

def _tasmota_enabled(app) -> bool:
    tcfg = (app.state.cfg or {}).get("tasmota")
    return bool(tcfg and tcfg.get("enabled"))

def _tasmota_power_control(app) -> bool:
    tcfg = (app.state.cfg or {}).get("tasmota")
    return bool(tcfg and tcfg.get("power_control"))

def _tasmota_params(app):
    tcfg = (app.state.cfg or {}).get("tasmota") or {}
    host = tcfg.get("host")
    user = tcfg.get("user") or None
    pw   = tcfg.get("pass") or None
    to   = int(tcfg.get("timeout") or 5)
    return host, user, pw, to

def _ensure_panel_on_and_push_layout(app):
    try:
        if _tasmota_enabled(app) and _tasmota_power_control(app):
            host, user, pw, to = _tasmota_params(app)
            try:
                stat = get_power(host, user=user, password=pw, timeout=to)
                p = (stat or {}).get("state") == "ON" or (stat or {}).get("POWER") \
                    or ((stat or {}).get("StatusSTS") or {}).get("POWER")
                log.debug(f"Power State: {p}, {stat}, Host {host}")
            except Exception:
                p = False

            if not p:
                set_power(host, "on", user=user, password=pw, timeout=to)
                log.info("Switching on Panel")
                time.sleep(_tasmota_power_wait_s(app))

        send_active_layout(app, line_nums=LINE_NUMS)
    except Exception as e:
        log.warning("Panel prep/layout failed: %s", e)


def _cancel_off_timer(app):
    t = getattr(app.state, "TASMOTA_OFF_TIMER", None)
    if t:
        try: t.cancel()
        except Exception: pass
    app.state.TASMOTA_OFF_TIMER = None

def _schedule_panel_off_in_min(app, min):
    _cancel_off_timer(app)

    log.info(f"panel auto-off: scheduling off via tasmota in {min} Minutes")
    def _maybe_power_off():
        try:
            s = app.state.PLAYER.get_state()
            if s and s.get("active"):
                return
            if not _tasmota_power_control(app):
                return
            
            host, user, pw, to = _tasmota_params(app)
            try:                
                stat = get_power(host, user=user, password=pw, timeout=to)
                is_on = (stat or {}).get("state") == "ON" or (stat or {}).get("POWER") or ((stat or {}).get("StatusSTS") or {}).get("POWER")                
                log.info(f"panel auto-off: panel state is: {is_on}")
            except Exception:
                is_on = False

            if is_on:
                set_power(host, "off", user=user, password=pw, timeout=to)
                log.info("panel auto-off: turned OFF after inactivity")
        except Exception as e:
            log.warning("panel auto-off failed: %s", e)
        finally:
            app.state.TASMOTA_OFF_TIMER = None

    t = threading.Timer(min * 60, _maybe_power_off)  
    t.daemon = True
    t.start()
    app.state.TASMOTA_OFF_TIMER = t


@asynccontextmanager
async def lifespan(app: FastAPI):
    _attach_uvicorn_file_handlers()

    app.state.cfg = cfg_load()
    app.state.TASMOTA_OFF_TIMER = None
    app.state.LED = LED
    app.state.PLAYER = PLAYER
    app.state.MQTT = None
    app.state.SELECTED_PLAYLIST = ""
    if SCHED:
        app.state.SCHED = SCHED

    if not PLAYER.is_alive():
        PLAYER.start()
        app.state.PLAYER.on_playlist_start = lambda: (
            _cancel_off_timer(app),
            _ensure_panel_on_and_push_layout(app)
        )
        log.info("Player thread started")

    send_active_layout(app, line_nums=LINE_NUMS)

    # ---------- MQTT starten & Callback setzen ----------
    mqtt_enabled = (app.state.cfg.get("mqtt", {}).get("enabled") 
                    if isinstance(app.state.cfg.get("mqtt"), dict) else False)
    if mqtt_enabled:
        try:
            app.state.MQTT = ScreenyMqtt(app)
            app.state.MQTT.start()
            log.info("MQTT bridge started")

            def _np(title, playlist, file_or_token):
                try:
                    app.state.MQTT.publish_now_playing(title=title, playlist=playlist, file_or_token=file_or_token)
                    app.state.MQTT._publish_now_with_image(name=playlist or "", pl={"name": playlist or ""})
                except Exception:
                    pass
            app.state.PLAYER.on_now_playing = _np

            try:
                playlists = list_playlists()
            except Exception:
                playlists = []

            # Discovery (ohne statische Kamera-URL; HA rendert dort keine Templates)
            app.state.MQTT.announce_discovery(playlists, thumb_url=None)

            # Library publizieren
            app.state.MQTT.publish_library(_count_media(), playlists)

            # Initialzustand
            try:
                s = app.state.PLAYER.get_state()
                item = (s or {}).get("item") or {}
                if s and s.get("active") and item:
                    app.state.MQTT.publish_now_playing(
                        title=item.get("title") or item.get("file"),
                        playlist=s.get("playlist"),
                        file_or_token=item.get("file"),
                    )
                    app.state.MQTT._publish_now_with_image(name=s.get("playlist") or "", pl={"name": s.get("playlist") or ""})
                else:
                    app.state.MQTT.publish_now_playing(None, None, None)
                    app.state.MQTT.publish_bytes_b64(f"{app.state.MQTT.base}/stat/now_playing_image", b"", retain=True, qos=1)
            except Exception:
                pass

        except Exception as e:
            log.warning("MQTT konnte nicht gestartet werden: %s", e)

    # ---------- Autostart ----------
    if app.state.cfg.get("autostart"):
        from .services.playlists import pl_load
        try:
            PLAYER.load(pl_load(app.state.cfg["autostart"]))
            log.info("autostart: %s", app.state.cfg["autostart"])
        except Exception as e:
            log.warning("Autostart-Fehler: %s", e)

    if SCHED:
        try:
            await SCHED.start()
            log.info("TasmotaScheduler gestartet")
        except Exception as e:
            log.warning("Scheduler konnte nicht starten: %s", e)

    try:
        _orig_stop = app.state.PLAYER.stop_playlist
        def _stop_and_schedule():
            try:
                _orig_stop()
            finally:
                _schedule_panel_off_in_min(app, _tasmota_auto_off_min(app))
        app.state.PLAYER.stop_playlist = _stop_and_schedule
        yield
    finally:
        try:
            if PLAYER.is_alive():
                PLAYER.stop()
                log.info("Player stop requested")
        except Exception as e:
            log.warning("Player stop error: %s", e)

        if SCHED:
            try:
                await SCHED.stop()
            except Exception as e:
                log.warning("Scheduler stop error: %s", e)

        if app.state.MQTT:
            try:
                app.state.MQTT.stop()
                log.info("MQTT bridge stopped")
            except Exception as e:
                log.warning("MQTT stop error: %s", e)

os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(PLAYLIST_DIR, exist_ok=True)

app = FastAPI(title="Screeny", lifespan=lifespan)

app.middleware("http")(build_http_error_mirror(
    logger_name="uvicorn.error",
    skip_prefixes=("/static/", "/media/", "/api/thumb"),
))

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(web_router)
app.include_router(schedule_router)
app.include_router(thumbs_router)
app.include_router(tasmota_router)
app.include_router(player_router)
app.include_router(media_router)
app.include_router(playlist_router)
app.include_router(config_router)
app.include_router(panels_router)