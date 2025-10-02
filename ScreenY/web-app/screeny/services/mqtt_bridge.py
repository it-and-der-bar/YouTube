import json
import logging
import os
import threading
from typing import Any, Callable, Dict, Optional, List, Tuple

import paho.mqtt.client as mqtt
from screeny.config import MEDIA_DIR

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

class ScreenyMqtt:
    """
    MQTT-Kommunikation inkl. Home Assistant Discovery.
    """

    def __init__(self, app):
        self.app = app
        cfg = getattr(app.state, "cfg", {}) or {}
        mcfg = (cfg.get("mqtt") or {})
        self.base = (mcfg.get("base") or os.getenv("SCREENY_MQTT_BASE") or "screeny").strip().strip("/")
        self.client_id = f"{self.base}-srv"

        self.host = mcfg.get("host") or os.getenv("SCREENY_MQTT_HOST", "localhost")
        self.port = int(mcfg.get("port") or os.getenv("SCREENY_MQTT_PORT", "1883"))
        self.user = mcfg.get("user") or os.getenv("SCREENY_MQTT_USER") or None
        self.password = mcfg.get("password") or os.getenv("SCREENY_MQTT_PASSWORD") or None

        self._cli = mqtt.Client(client_id=self.client_id, clean_session=True)
        if self.user:
            self._cli.username_pw_set(self.user, self.password)
        self._cli.will_set(f"{self.base}/stat/online", payload="offline", qos=1, retain=True)

        self._cli.on_connect = self._on_connect
        self._cli.on_message = self._on_message
        self._cli.on_disconnect = self._on_disconnect

        self._thread: Optional[threading.Thread] = None
        self._subscriptions: Dict[str, Callable[[str], None]] = {}

    # ---------- Public API ----------

    def start(self) -> None:
        self._cli.connect(self.host, self.port, keepalive=30)
        self._thread = threading.Thread(target=self._cli.loop_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self._cli.publish(f"{self.base}/stat/online", "offline", qos=1, retain=True)
        finally:
            self._cli.disconnect()

    def publish_json(self, topic: str, payload: Dict[str, Any], retain: bool = False, qos: int = 0) -> None:
        self._cli.publish(topic, json.dumps(payload, ensure_ascii=False), qos=qos, retain=retain)

    def publish_text(self, topic: str, payload: str, retain: bool = False, qos: int = 0) -> None:
        self._cli.publish(topic, payload, qos=qos, retain=retain)

    def publish_bytes_b64(self, topic: str, data: bytes, retain: bool = False, qos: int = 0) -> None:
        import base64
        self._cli.publish(topic, base64.b64encode(data), qos=qos, retain=retain)

    def subscribe(self, topic: str, handler: Callable[[str], None]) -> None:
        self._subscriptions[topic] = handler
        self._cli.subscribe(topic, qos=0)

    # ---------- HA Discovery ----------

    def _device_block(self) -> Dict[str, Any]:
        return {
            "identifiers": [f"{self.base}_device"],
            "name": "Screeny",
            "manufacturer": "Screeny",
            "model": "LED Wall Controller",
        }

    def announce_discovery(self, playlists: List[str], thumb_url: Optional[str]) -> None:
        """
        Entitäten via MQTT Discovery bei Home Assistant anmelden.
        """
        disc = "homeassistant"
        device = {
            "identifiers": [f"{self.base}_device"],
            "name": "Screeny",
            "manufacturer": "Screeny",
            "model": "LED Wall Controller",
        }

        # Select: Playlist-Auswahl
        self.publish_json(
            f"{disc}/select/{self.base}/playlist/config",
            {
                "name": "Screeny Playlist",
                "unique_id": f"{self.base}_playlist",
                "command_topic": f"{self.base}/cmnd/playlist/select",
                "state_topic": f"{self.base}/stat/playlist/selected",
                "options": playlists or [],
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "entity_category": "config",
                "device": device,
            },
            retain=True, qos=1
        )

        # Buttons: Start/Stop
        self.publish_json(
            f"{disc}/button/{self.base}/start/config",
            {
                "name": "Screeny Start",
                "unique_id": f"{self.base}_start",
                "command_topic": f"{self.base}/cmnd/playlist/start",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "device": device,
            },
            retain=True, qos=1
        )
        self.publish_json(
            f"{disc}/button/{self.base}/stop/config",
            {
                "name": "Screeny Stop",
                "unique_id": f"{self.base}_stop",
                "command_topic": f"{self.base}/cmnd/playlist/stop",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "device": device,
            },
            retain=True, qos=1
        )

        self.publish_json(
            f"{disc}/button/{self.base}/next/config",
            {
                "name": "Screeny Next",
                "unique_id": f"{self.base}_next",
                "command_topic": f"{self.base}/cmnd/player/next",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "device": device,
            },
            retain=True, qos=1
        )
        self.publish_json(
            f"{disc}/button/{self.base}/prev/config",
            {
                "name": "Screeny Prev",
                "unique_id": f"{self.base}_prev",
                "command_topic": f"{self.base}/cmnd/player/prev",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "device": device,
            },
            retain=True, qos=1
        )

        # Sensoren: Medien / Playlists / Now Playing (Text)
        self.publish_json(
            f"{disc}/sensor/{self.base}/media_count/config",
            {
                "name": "Screeny Medien",
                "unique_id": f"{self.base}_media_count",
                "state_topic": f"{self.base}/stat/library",
                "value_template": "{{ value_json.media_count }}",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "device": device,
            },
            retain=True, qos=1
        )
        self.publish_json(
            f"{disc}/sensor/{self.base}/playlist_count/config",
            {
                "name": "Screeny Playlists",
                "unique_id": f"{self.base}_playlist_count",
                "state_topic": f"{self.base}/stat/library",
                "value_template": "{{ value_json.playlist_count }}",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "device": device,
            },
            retain=True, qos=1
        )
        self.publish_json(
            f"{disc}/sensor/{self.base}/now_playing/config",
            {
                "name": "Screeny Now Playing",
                "unique_id": f"{self.base}_now_playing",
                "state_topic": f"{self.base}/stat/now_playing",
                "value_template": "{{ value_json.title if value_json }}",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "device": device,
            },
            retain=True, qos=1
        )

        # MQTT Camera – empfängt Base64-JPEG auf Topic <base>/stat/now_playing_image
        self.publish_json(
            f"{disc}/camera/{self.base}/thumb/config",
            {
                "name": "Screeny Thumbnail",
                "unique_id": f"{self.base}_thumb",
                "topic": f"{self.base}/stat/now_playing_image",
                "image_encoding": "b64",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "device": device,
            },
            retain=True, qos=1
        )
        # --- Panels: Layout, Auflösung, Attribute ---
        self.publish_json(
            f"{disc}/sensor/{self.base}/panel_layout/config",
            {
                "name": "Screeny Panel Layout",
                "unique_id": f"{self.base}_panel_layout",
                "state_topic": f"{self.base}/stat/panel/layout",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "entity_category": "diagnostic",
                "device": device,
                "json_attributes_topic": f"{self.base}/stat/panel/attrs",
            },
            retain=True, qos=1
        )

        self.publish_json(
            f"{disc}/sensor/{self.base}/panel_resolution/config",
            {
                "name": "Screeny Panel Auflösung",
                "unique_id": f"{self.base}_panel_resolution",
                "state_topic": f"{self.base}/stat/panel/resolution",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "entity_category": "diagnostic",
                "device": device,
                "json_attributes_topic": f"{self.base}/stat/panel/attrs",
            },
            retain=True, qos=1
        )

        self.publish_json(
            f"{disc}/sensor/{self.base}/panel_tiles/config",
            {
                "name": "Screeny Panel Tiles",
                "unique_id": f"{self.base}_panel_tiles",
                "state_topic": f"{self.base}/stat/panel/tiles",
                "availability": [{"topic": f"{self.base}/stat/online"}],
                "entity_category": "diagnostic",
                "device": device,
                "json_attributes_topic": f"{self.base}/stat/panel/attrs",
            },
            retain=True, qos=1
        )


        # Online-Flag
        self.publish_text(f"{self.base}/stat/online", "online", retain=True, qos=1)

    # ---------- Internals ----------

    def _on_connect(self, client, userdata, flags, rc):
        # Commands
        self.subscribe(f"{self.base}/cmnd/playlist/select", self._h_select)
        self.subscribe(f"{self.base}/cmnd/playlist/start", self._h_start)
        self.subscribe(f"{self.base}/cmnd/playlist/stop", self._h_stop)
        self.subscribe(f"{self.base}/cmnd/player/next", self._h_next)
        self.subscribe(f"{self.base}/cmnd/player/prev", self._h_prev)

        # Announce online
        self.publish_text(f"{self.base}/stat/online", "online", retain=True, qos=1)

    def _on_disconnect(self, client, userdata, rc):
        pass

    def _on_message(self, client, userdata, msg):
        handler = self._subscriptions.get(msg.topic)
        if handler:
            try:
                handler(msg.payload.decode("utf-8").strip())
            except Exception:
                pass

    # ---------- Command-Handler ----------

    def _h_select(self, payload: str):
        name = payload
        self.publish_text(f"{self.base}/stat/playlist/selected", name or "")
        self.app.state.SELECTED_PLAYLIST = name
        log.info(f"Selected Playlist: {name}")

    def _h_start(self, payload: str):
        raw = (payload or "").strip().strip('"').strip("'")
        if raw.upper() in ("PRESS", "ON", "OFF", "TOGGLE", ""):
            raw = ""
        name = raw or getattr(self.app.state, "SELECTED_PLAYLIST", "") or (self.app.state.cfg or {}).get("autostart", "")
        if not name:
            return
        try:
            from screeny.services.playlists import pl_load
            pl = pl_load(name)

            if self.app.state.PLAYER is None or not self.app.state.PLAYER.is_alive():
                from screeny.services.player import Player
                self.app.state.PLAYER = Player(self.app.state.LED)
                self.app.state.PLAYER.start()
            try:
                from utils.layout_store import send_active_layout
                from ..config import LINE_NUMS
                send_active_layout(self.app, line_nums=LINE_NUMS)
            except Exception:
                pass

            self.app.state.PLAYER.load(pl)

            try:
                state = self.app.state.PLAYER.get_state() if hasattr(self.app.state.PLAYER, "get_state") else None
                item = (state or {}).get("item") if isinstance(state, dict) else None
                file_or_token = (item or {}).get("file")
                title = (item or {}).get("title") or getattr(pl, "name", name)
                self.publish_now_playing(title=title, playlist=name, file_or_token=file_or_token)
            except Exception:
                pass

            self.app.state.SELECTED_PLAYLIST = name

        except Exception:
            pass

    def _h_stop(self, payload: str):
        try:
            if self.app.state.PLAYER:
                self.app.state.PLAYER.stop_playlist()
                self.app.state.LED.clear((0,0,0))
        except Exception:
            pass
        try:
            self.publish_now_playing(title=None, playlist=None, file_or_token=None)
            self.publish_bytes_b64(f"{self.base}/stat/now_playing_image", _blank_jpeg(), retain=True, qos=1)
        except Exception:
            pass

    def _h_next(self, payload: str):
        try:
            if self.app.state.PLAYER:
                self.app.state.PLAYER.next()
        except Exception:
            pass

    def _h_prev(self, payload: str):
        try:
            if self.app.state.PLAYER:
                self.app.state.PLAYER.prev()
        except Exception:
            pass

    # ---------- Helpers für Status ----------

    def publish_library(self, media_count: int, playlists: List[str]):
        self.publish_json(
            f"{self.base}/stat/library",
            {"media_count": media_count, "playlist_count": len(playlists), "playlists": playlists},
            retain=True,
            qos=1,
        )

    def publish_now_playing(self, title: Optional[str], playlist: Optional[str], file_or_token: Optional[str]):
        payload: Dict[str, Any] = {}
        if title:
            payload = {"title": title, "playlist": playlist, "file": file_or_token}
        self.publish_json(f"{self.base}/stat/now_playing", payload, retain=False, qos=0)

    def _publish_now_with_image(self, name: str, pl: Dict[str, Any]) -> None:
        """
        Liest den aktuellen Player-State, published Textdaten und EIN Bild (JPEG Base64) auf now_playing_image.
        """
        try:
            state = self.app.state.PLAYER.get_state() if hasattr(self.app.state.PLAYER, "get_state") else None
            item = (state or {}).get("item") if isinstance(state, dict) else None
            file_or_token = (item or {}).get("file")
            title = (item or {}).get("title") or pl.get("name") or name

            file_or_token = (item or {}).get("file")
            bn = os.path.basename(file_or_token) if file_or_token else ""
            title = (item or {}).get("title") or (bn or None)
        except Exception:
            state = None
            item = None
            file_or_token = None
            title = name

        # 1) Text-State
        self.publish_now_playing(title=title, playlist=name, file_or_token=file_or_token)

        # 2) Bild nur EINMAL schicken
        try:
            if not file_or_token:
                self.publish_bytes_b64(f"{self.base}/stat/now_playing_image", _blank_jpeg(), retain=True, qos=1)
                return
            jpeg = _make_thumbnail_jpeg(file_or_token)
            if jpeg:
                self.publish_bytes_b64(f"{self.base}/stat/now_playing_image", jpeg, retain=True, qos=1)
            else:
                self.publish_bytes_b64(f"{self.base}/stat/now_playing_image", _blank_jpeg(), retain=True, qos=1)
        except Exception:
            pass
        
    def publish_panel_info(self, layout: Optional[Dict[str, Any]] = None):
        """
        Published Layout/Resolution/Attributes der aktuellen LED-Konfiguration
        auf:
        - {base}/stat/panel/layout        -> "2x1"
        - {base}/stat/panel/resolution    -> "256x128"
        - {base}/stat/panel/tiles         -> "N" (Anzahl)
        - {base}/stat/panel/attrs         -> JSON mit Details
        """
        led = getattr(self.app.state, "LED", None)
        tiles = []
        total_w = total_h = 0
        panel_w = panel_h = 0
        grid_cols = grid_rows = 0

        try:
            if layout:
                grid_cols = int(layout.get("grid_cols") or 1)
                grid_rows = int(layout.get("grid_rows") or 1)
                panel_w   = int(layout.get("panel_w") or 128)
                panel_h   = int(layout.get("panel_h") or 128)
                tiles     = layout.get("tiles") or []
                total_w   = grid_cols * panel_w
                total_h   = grid_rows * panel_h
            elif led is not None:
                total_w = int(getattr(led, "screen_w", 0) or 0)
                total_h = int(getattr(led, "screen_h", 0) or 0)
                tiles   = list(getattr(led, "tiles", []) or [])
                if tiles:
                    t0 = tiles[0]
                    panel_w = int(t0.get("w", 128))
                    panel_h = int(t0.get("h", 128))
                    grid_cols = max(1, total_w // max(1, panel_w))
                    grid_rows = max(1, total_h // max(1, panel_h))
        except Exception:
            pass

        layout_str = f"{grid_cols}x{grid_rows}" if grid_cols and grid_rows else ""
        res_str    = f"{total_w}x{total_h}" if total_w and total_h else ""
        tiles_cnt  = len(tiles)

        attrs = {
            "grid_cols": grid_cols, "grid_rows": grid_rows,
            "panel_w": panel_w, "panel_h": panel_h,
            "total_w": total_w, "total_h": total_h,
            "tiles_count": tiles_cnt,
        }

        # State Topics
        self.publish_text(f"{self.base}/stat/panel/layout", layout_str, retain=True, qos=1)
        self.publish_text(f"{self.base}/stat/panel/resolution", res_str, retain=True, qos=1)
        self.publish_text(f"{self.base}/stat/panel/tiles", str(tiles_cnt), retain=True, qos=1)
        self.publish_json(f"{self.base}/stat/panel/attrs", attrs, retain=True, qos=1)


# ---------------- Thumbnail-Helfer ----------------

def _blank_jpeg(w: int = 1, h: int = 1) -> bytes:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", arr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return buf.tobytes() if ok else b""


def _resolve_media_path(rel: str) -> Optional[str]:
    if not rel:
        return None
    s = rel.strip()
    if s.startswith("text://"):
        return None
    if "://" in s:
        return None
    if os.path.isabs(s):
        return s if os.path.exists(s) else None
    p = os.path.join(MEDIA_DIR, s)
    return p if os.path.exists(p) else None


def _make_thumbnail_jpeg(file_or_token: str, size: Tuple[int, int] = (256, 256)) -> Optional[bytes]:
    """
    Liefert EIN JPEG:
      - Bild: scaled/letterboxed
      - Video: 1 Frame (Mitte)
      - text://... : gerendertes Vorschau-Bild
      - Stream/URL: None
    """
    W, H = size

    if (file_or_token or "").startswith("text://"):
        try:
            import base64, json
            payload = (file_or_token or "")[7:]
            cfg = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
            text = cfg.get("text") or ""
            fg = cfg.get("color") or "#ffffff"
            bg = cfg.get("bg") or "#000000"
            font_size = int(cfg.get("font_size") or 24)

            def parse_hex(c, default):
                c = (c or "").strip()
                if c.startswith("#"): c = c[1:]
                try:
                    return (int(c[0:2],16), int(c[2:4],16), int(c[4:6],16))
                except Exception:
                    return default

            fg_rgb = parse_hex(fg, (255,255,255))
            bg_rgb = parse_hex(bg, (0,0,0))

            img = Image.new("RGB", (W, H), bg_rgb)
            draw = ImageDraw.Draw(img)

            CANDS = [
                "DejaVuSans.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "Arial Unicode.ttf",
            ]
            font = None
            for fp in CANDS:
                try:
                    font = ImageFont.truetype(fp, size=font_size); break
                except Exception:
                    continue
            if font is None:
                font = ImageFont.load_default()

            tw, th = font.getbbox(text)[2:]
            x = max(0, (W - tw)//2)
            y = max(0, (H - th)//2)
            draw.text((x, y), text, fill=fg_rgb, font=font)

            rgb = np.array(img)
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            return buf.tobytes() if ok else None
        except Exception:
            return None

    if "://" in (file_or_token or ""):
        return None

    fp = _resolve_media_path(file_or_token or "")
    if not fp:
        return None

    ext = os.path.splitext(fp)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"):
        img = cv2.imread(fp, cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        s = min(W / w, H / h)
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((H, W, 3), dtype=np.uint8)
        y0 = (H - nh) // 2
        x0 = (W - nw) // 2
        canvas[y0:y0+nh, x0:x0+nw] = resized
        ok, buf = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return buf.tobytes() if ok else None

    cap = cv2.VideoCapture(fp)
    if not cap.isOpened():
        return None
    try:
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        target = max(0, frames // 2)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        ok, frame = cap.read()
        if not ok or frame is None:
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
        h, w = frame.shape[:2]
        s = min(W / w, H / h)
        nw, nh = max(1, int(w * s)), max(1, int(h * s))
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
        canvas = np.zeros((H, W, 3), dtype=np.uint8)
        y0 = (H - nh) // 2
        x0 = (W - nw) // 2
        canvas[y0:y0+nh, x0:x0+nw] = resized
        ok, buf = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return buf.tobytes() if ok else None
    finally:
        cap.release()
