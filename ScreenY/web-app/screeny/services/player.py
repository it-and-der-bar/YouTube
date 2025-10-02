import logging
import math
import threading, time, os
import cv2, numpy as np
import random
import base64, json

from PIL import Image, ImageDraw, ImageFont 

from .weather import WX_CITY, WeatherCache, render_clock_panel
from ..config import MEDIA_DIR
from .playlists import is_image, is_video, is_stream
from .text_renderer import TextRenderer

log = logging.getLogger(__name__)

class Player(threading.Thread):
    def __init__(self, led):
        super().__init__(daemon=True)
        self.led = led
        self._stop = threading.Event()
        self._abort = threading.Event()
        self._lock = threading.RLock()
        self._cur = None
        self._ver = 0
        self._wx = WeatherCache()
        self._state_lock = threading.Lock()
        self._state = {
            "active": False,
            "playlist": None,
            "index": -1,
            "total": 0,
            "item": None,
            "started": None,
        }
        self._req = None
        self._text = TextRenderer(self.led)

        self.on_now_playing = None
        self.on_playlist_start = None

    # --- State API ---
    def _set_state(self, **kw):
        with self._state_lock:
            self._state.update(kw)

    def get_state(self):
        with self._state_lock:
            s = dict(self._state)
            if s.get("item"):
                s["item"] = dict(s["item"])
            return s

    def stop_playlist(self):
        with self._lock:
            self._cur = None
            self._ver += 1
            self._req = None
        self._abort.set()
        self._set_state(active=False, playlist=None, index=-1, total=0, item=None, started=None)


    def load(self, playlist_dict):
        with self._lock:
            self._cur = playlist_dict
            self._ver += 1
            name = (playlist_dict or {}).get("name")
            total = len((playlist_dict or {}).get("items", []))
        self._set_state(active=bool(total), playlist=name, index=0 if total else -1, total=total, item=None, started=time.time())
        self._abort.set()

    def next(self):
        with self._lock:
            self._req = "next"
            self._abort.set()

    def prev(self):
        with self._lock:
            self._req = "prev"
            self._abort.set()

    def _emit_now_playing(self, *, title, playlist, file_or_token):
        cb = getattr(self, "on_now_playing", None)
        if cb:
            try:
                cb(title, playlist, file_or_token)
            except Exception as e:
                log.debug("on_now_playing callback failed: %s", e)

    def run(self):
        pos = 0
        order = []
        last_ver = -1

        while not self._stop.is_set():
            time.sleep(0.05)

            with self._lock:
                pl = self._cur
                cur_ver = self._ver

            if not pl or not pl.get("items"):
                self._set_state(active=False, item=None)
                continue

            items = pl["items"]
            p_mode = (pl.get("mode") or "repeat").lower()
            if p_mode not in ("repeat", "random", "once"):
                p_mode = "repeat"

            if cur_ver != last_ver:
                last_ver = cur_ver
                pos = 0
                self._req = None
                self._abort.clear()
                order = list(range(len(items)))
                if p_mode == "random":
                    random.shuffle(order)

                self._set_state(
                    active=True,
                    playlist=pl.get("name"),
                    playlist_mode=p_mode,
                    total=len(items),
                    started=time.time()
                )
                try:
                    cb = getattr(self, "on_playlist_start", None)
                    if cb:
                        cb()  # darf blockieren (z.B. 15s warten)
                except Exception as e:
                    log.warning("on_playlist_start failed: %s", e)

            if not order:
                self._set_state(active=False, item=None, index=-1)
                continue

            if pos < 0: pos = 0
            if pos >= len(order):
                if p_mode == "repeat":
                    pos = 0
                elif p_mode == "random":
                    random.shuffle(order)
                    pos = 0
                else:  # once
                    self._set_state(active=False, item=None, index=-1)
                    continue

            real_idx = order[pos]
            it = items[real_idx]
            raw = (it.get("file") or "").strip()
            if not raw and not it.get("text"):
                pos += 1
                continue

            if "://" in raw or os.path.isabs(raw):
                src = raw; local = False
            else:
                src = os.path.join(MEDIA_DIR, raw); local = True

            mode = (it.get("mode") or "fill").lower()
            if mode not in ("fill", "fit"): mode = "fill"
            duration = int(it.get("duration", 0) or 0)
            loops = max(1, int(it.get("loop", 1) or 1))

            # Typ
            if raw.startswith("clock://"):
                typ = "clock"; local = False
            elif is_stream(src):
                typ = "stream"
            elif is_video(src):
                typ = "video"
            elif is_image(src):
                typ = "image"
            elif it.get("text") or raw.lower().startswith("text://"):
                typ = "text" 
            else:
                typ = "other"

            state_file = raw or it.get("file") or ""

            self._set_state(
                active=True,
                playlist=pl.get("name"),
                playlist_mode=p_mode,
                index=real_idx,
                total=len(items),
                item={"file": state_file, "type": typ, "local": local, "mode": mode, "duration": duration, "loop": loops}
            )

            try:
                title = it.get("title") or (os.path.basename(state_file) if state_file and "://" not in state_file else state_file) or typ
                self._emit_now_playing(title=title, playlist=pl.get("name"), file_or_token=state_file)
            except Exception as _e:
                log.debug("emit now_playing failed: %s", _e)

            try:
                if typ == "stream":
                    max_seconds = None if duration <= 0 else float(duration * loops)
                    self.led.play_stream(
                        src, fps_limit=25.0, mode=mode,
                        max_seconds=max_seconds,
                        should_abort=lambda: self._stop.is_set() or self._abort.is_set()
                    )

                elif typ == "video":
                    for _ in range(loops):
                        self.led.play_video(
                            src, fps_limit=None, mode=mode,
                            should_abort=lambda: self._stop.is_set() or self._abort.is_set()
                        )
                        if self._stop.is_set() or self._abort.is_set():
                            break

                elif typ == "clock":
                    variant = (raw.split("://", 1)[1] or "time").lower()
                    show_date = ("time_date" in variant)
                    show_wx = ("weather" in variant)
                    t_end = None if (duration <= 0) else (time.time() + duration * loops)
                    while True:
                        if self._stop.is_set() or self._abort.is_set(): break
                        wx = None
                        if show_wx:
                            try: wx = self._wx.get()
                            except Exception: wx = None
                        w, h = self.led.screen_w, self.led.screen_h
                        img = render_clock_panel(size=(w, h),
                                                 show_seconds=True, show_date=show_date,
                                                 weather=(wx if show_wx else None), city=WX_CITY)
                        arr = np.array(img, dtype=np.uint8)[:, :, ::-1]
                        self.led.send_frame(arr, sync_profile="still")
                        if t_end and time.time() >= t_end: break
                        now = time.time()
                        time.sleep(max(0.05, 1.0 - (now - math.floor(now))))

                elif typ == "text":
                    cfg = TextRenderer.build_text_cfg(raw, it)
                    log.debug(f"Decoded text object: {cfg}")

                    for _ in range(loops):
                        for frame_rgb in self._text.render_once(
                            text=cfg["text"],
                            color=cfg["color"],
                            bg=cfg["bg"],
                            font_size=cfg["font_size"],
                            speed_px_s=cfg["speed_px_s"],
                            duration=cfg["duration"],
                            align_h=cfg["align_h"],   
                            align_v=cfg["align_v"],   
                        ):
                            self.led.send_frame(frame_rgb[:, :, ::-1], sync_profile="still", mode=mode)
                            if self._stop.is_set() or self._abort.is_set():
                                break
                        if self._stop.is_set() or self._abort.is_set():
                            break

                else:
                    if not os.path.exists(src):
                        pos += 1
                        continue
                    frame = cv2.imread(src, cv2.IMREAD_UNCHANGED)
                    if frame is None:
                        from PIL import Image as PILImage
                        img = PILImage.open(src).convert("RGB")  # RGB
                        frame = np.array(img)[..., ::-1]         # -> BGR

                    if frame.ndim == 2:
                        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    elif frame.shape[2] == 4:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                    H, W = getattr(self.led, "screen_h", 0) or (getattr(self.led, "grid_rows", 1) * getattr(self.led, "panel_h", 128)), \
                        getattr(self.led, "screen_w", 0) or (getattr(self.led, "grid_cols", 1) * getattr(self.led, "panel_w", 128))
                    if not H or not W:
                        H, W = 128, 128  # Fallback

                    src_h, src_w = frame.shape[:2]
                    if mode == "fit":
                        # Contain: Einpassen + Letterbox
                        s = min(W / src_w, H / src_h)
                        nw, nh = max(1, int(round(src_w * s))), max(1, int(round(src_h * s)))
                        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
                        canvas = np.zeros((H, W, 3), dtype=np.uint8)
                        y0 = (H - nh) // 2
                        x0 = (W - nw) // 2
                        canvas[y0:y0+nh, x0:x0+nw] = resized
                        out = canvas
                    else:
                        # Cover: Zuschneiden nach Skalierung
                        s = max(W / src_w, H / src_h)
                        nw, nh = max(1, int(round(src_w * s))), max(1, int(round(src_h * s)))
                        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
                        # mittig auf Zielgröße croppen
                        y0 = max(0, (nh - H) // 2)
                        x0 = max(0, (nw - W) // 2)
                        out = resized[y0:y0+H, x0:x0+W]

                    # Jetzt exakt (H,W,3)
                    self.led.send_frame(out, sync_profile="still", mode=mode)

                    wait_s = max(1, duration or 10)
                    t_end = time.time() + (wait_s * loops)
                    while time.time() < t_end:
                        if self._stop.is_set() or self._abort.is_set(): break
                        time.sleep(0.05)

            except Exception as e:
                import logging; logging.getLogger(__name__).warning("play error: %s", e)

            if self._stop.is_set():
                self._set_state(active=False)
                break

            req = None
            with self._lock:
                if self._abort.is_set():
                    self._abort.clear()
                    req = self._req
                    self._req = None

            if req == "prev":
                pos = (pos - 1) % len(order)
            elif req == "next":
                pos = (pos + 1) % len(order)
            else:
                pos += 1
                if pos >= len(order):
                    if p_mode == "repeat":
                        pos = 0
                    elif p_mode == "random":
                        random.shuffle(order)
                        pos = 0
                    else:
                        self._set_state(active=False, item=None, index=-1)
                        continue
