import time, json, base64
from typing import Generator, Tuple
import numpy as np
from PIL import Image, ImageDraw, ImageFont


class TextRenderer:
    """
    Rendert Text (statisch/scrollend) als RGB-Frames (H x W x 3).
    - Ausrichtung horizontal: align_h = left|center|right
    - Ausrichtung vertikal:   align_v = top|middle|bottom
    - Bearing (t) wird korrekt berücksichtigt -> nichts wird abgeschnitten.
    """

    def __init__(self, led):
        self.led = led

    @staticmethod
    def parse_token_url(raw: str) -> dict | None:
        """text://<base64url(json)> -> dict | None"""
        if not (raw or "").lower().startswith("text://"):
            return None
        try:
            tok = raw.split("://", 1)[1]
            tok = tok.replace("-", "+").replace("_", "/")
            tok += "=" * ((4 - len(tok) % 4) % 4)
            return json.loads(base64.b64decode(tok.encode("ascii")).decode("utf-8"))
        except Exception:
            return None

    @staticmethod
    def build_text_cfg(raw: str, it: dict) -> dict:
        """
        Merged Playlist-Item + (optional) Token aus URL in eine saubere cfg.
        Alles hier – NICHT im Player.
        """
        cfg = {
            "text":       str(it.get("text") or ""),
            "color":      it.get("color") or "#ffffff",
            "bg":         it.get("bg") or "#000000",
            "font_size":  int(it.get("font_size") or 24),
            "speed_px_s": int(it.get("speed_px_s") or 40),
            "duration":   int(it.get("duration", 0) or 0),
            "align_h":    (it.get("align_h") or "center").lower(),
            "align_v":    (it.get("align_v") or "middle").lower(),
        }

        tok = TextRenderer.parse_token_url(raw)
        if tok:
            if "text" in tok:       cfg["text"] = str(tok.get("text") or cfg["text"])
            if "color" in tok:      cfg["color"] = tok["color"] or cfg["color"]
            if "bg" in tok:         cfg["bg"]    = tok["bg"]    or cfg["bg"]
            if "font_size" in tok:  cfg["font_size"]  = int(tok["font_size"] or cfg["font_size"])
            if "speed_px_s" in tok: cfg["speed_px_s"] = int(tok["speed_px_s"] or cfg["speed_px_s"])
            if "duration" in tok:
                try: cfg["duration"] = int(tok["duration"])
                except Exception: pass
            if "align_h" in tok:
                cfg["align_h"] = (tok["align_h"] or cfg["align_h"]).lower()
            if "align_v" in tok:
                cfg["align_v"] = (tok["align_v"] or cfg["align_v"]).lower()

        if cfg["align_h"] not in ("left", "center", "right"):
            cfg["align_h"] = "center"
        if cfg["align_v"] not in ("top", "middle", "bottom"):
            cfg["align_v"] = "middle"
            
        return cfg

    @staticmethod
    def _parse_color(s: str | None, default: Tuple[int,int,int]=(255,255,255)) -> Tuple[int,int,int]:
        z = (s or "").strip()
        if z.startswith("#"):
            z = z[1:]
        try:
            if len(z) == 3:
                return (int(z[0]*2, 16), int(z[1]*2, 16), int(z[2]*2, 16))
            if len(z) == 6:
                return (int(z[0:2], 16), int(z[2:4], 16), int(z[4:6], 16))
        except Exception:
            pass
        names = {
            "white": (255,255,255), "black": (0,0,0), "red": (255,0,0),
            "green": (0,255,0), "blue": (0,0,255), "yellow": (255,255,0),
            "cyan": (0,255,255), "magenta": (255,0,255)
        }
        return names.get((s or "").lower(), default)

    @staticmethod
    def _load_font(size: int) -> ImageFont.FreeTypeFont:
        candidates = [
            "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "Arial Unicode.ttf",
        ]
        for fp in candidates:
            try:
                return ImageFont.truetype(fp, size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def render_once(
        self,
        *,
        text: str,
        color="#ffffff",
        bg="#000000",
        font_size=24,
        speed_px_s=40,
        duration=0,
        align_h="center",
        align_v="middle",
    ) -> Generator[np.ndarray, None, None]:

        W = getattr(self.led, "screen_w", None) or (getattr(self.led, "grid_cols", 1) * getattr(self.led, "panel_w", 128))
        H = getattr(self.led, "screen_h", None) or (getattr(self.led, "grid_rows", 1) * getattr(self.led, "panel_h", 64))
        if not W or not H:
            W, H = 128, 64

        fg = self._parse_color(color, (255, 255, 255))
        bgc = self._parse_color(bg, (0, 0, 0))
        font = self._load_font(int(font_size or 24))

        fps = 20.0
        interval = 1.0 / fps
        spacing = max(0, int(font.size * 0.25))

        tmp = Image.new("RGB", (1, 1))
        d = ImageDraw.Draw(tmp)
        lines = (text or "").splitlines() or [""]

        L, T, R, B = d.multiline_textbbox((0, 0), text or "", font=font, spacing=spacing, align="left")
        block_w_bbox = max(1, R - L)
        block_h_bbox = max(1, B - T)

        per_line = []
        max_line_w = 0
        for line in lines:
            l, t, r, b = d.textbbox((0, 0), line, font=font)  # einzelne Zeile
            w = r - l
            h = b - t
            per_line.append((w, h, l, t))
            if w > max_line_w:
                max_line_w = w

        if align_v == "top":
            y0 = -T
        elif align_v == "bottom":
            y0 = (H - block_h_bbox) - T
        else:
            y0 = (H - block_h_bbox) // 2 - T

        if max_line_w <= W:
            wait_s = max(1.0, float(duration or 10))
            t_end = time.time() + wait_s
            img = Image.new("RGB", (W, H), bgc)
            draw = ImageDraw.Draw(img)
            y = y0
            for (line, (w, h, l, t)) in zip(lines, per_line):
                if align_h == "left":
                    x = 0
                elif align_h == "right":
                    x = W - w
                else:  
                    x = (W - w) // 2
                draw.text((x - l, y), line, fill=fg, font=font)
                y += h + spacing
            frame = np.array(img, dtype=np.uint8)
            while time.time() < t_end:
                yield frame
                time.sleep(interval)
            return

        block_w = max_line_w
        block_h = block_h_bbox 
        text_img = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
        td = ImageDraw.Draw(text_img)

        y = -T
        for (line, (w, h, l, t)) in zip(lines, per_line):
            if align_h == "left":
                x = 0
            elif align_h == "right":
                x = block_w - w
            else:  
                x = (block_w - w) // 2
            td.text((x - l, y), line, fill=(fg[0], fg[1], fg[2], 255), font=font)
            y += h + spacing

        if align_v == "top":
            y_top = 0
        elif align_v == "bottom":
            y_top = H - block_h
        else:
            y_top = (H - block_h) // 2

        speed = max(10.0, float(speed_px_s or 40))
        total_px = block_w + W
        total_time = total_px / speed
        total_frames = max(1, int(total_time * fps))
        x_left = float(W)

        for _ in range(total_frames):
            img = Image.new("RGB", (W, H), bgc)
            img.paste(text_img, (int(round(x_left)), int(y_top)), text_img)
            yield np.array(img, dtype=np.uint8)
            x_left -= speed * interval
            time.sleep(interval)
