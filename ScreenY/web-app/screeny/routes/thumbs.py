import os
import cv2
import numpy as np
import hashlib
import logging
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse, JSONResponse, RedirectResponse

from ..config import MEDIA_DIR

from PIL import Image, ImageDraw, ImageFont
import base64, json

log = logging.getLogger(__name__)
router = APIRouter()

THUMB_DIR = os.path.join(MEDIA_DIR, ".thumbs")
os.makedirs(THUMB_DIR, exist_ok=True)

TARGET_W = 128
TARGET_H = 128
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}

def _safe_media_path(rel: str) -> Optional[str]:
    if not rel or rel.startswith(("/", "\\")):
        return None
    rel = rel.replace("\\", "/")
    norm = os.path.normpath(rel)
    if norm.startswith(".."):
        return None
    p = os.path.join(MEDIA_DIR, norm)
    return p if os.path.isfile(p) else None

def _thumb_path_key(key: str) -> str:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(THUMB_DIR, f"{h}.jpg")

def _thumb_path(src_abs: str, variant: str = "mid") -> str:
    st = os.stat(src_abs)
    key = f"file|{src_abs}|{st.st_mtime_ns}|{st.st_size}|{TARGET_W}x{TARGET_H}|{variant}"
    return _thumb_path_key(key)

def _center_letterbox(bgr: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    if h <= 0 or w <= 0:
        raise ValueError("invalid frame size")
    scale = min(target_w / w, target_h / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)  # schwarz
    y0 = (target_h - nh) // 2
    x0 = (target_w - nw) // 2
    canvas[y0:y0+nh, x0:x0+nw] = resized
    return canvas

def _encode_jpeg(bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return bytes(buf)

def _video_middle_frame(path: str) -> np.ndarray:
    cap = cv2.VideoCapture(path)
    if not cap or not cap.isOpened():
        raise RuntimeError("cannot open video")
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_count > 0:
            mid_idx = max(0, frame_count // 2)
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(mid_idx))
            ok, frame = cap.read()
            if ok and frame is not None:
                return frame

        duration_ms = float(cap.get(cv2.CAP_PROP_DURATION))
        if not duration_ms or np.isnan(duration_ms) or duration_ms <= 0:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            if fps > 0 and frame_count > 0:
                duration_ms = (frame_count / fps) * 1000.0
        if duration_ms and duration_ms > 0:
            mid_ms = duration_ms / 2.0
            cap.set(cv2.CAP_PROP_POS_MSEC, mid_ms)
            ok, frame = cap.read()
            if ok and frame is not None:
                return frame

        cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame

        raise RuntimeError("cannot read any frame")
    finally:
        cap.release()

def _b64url_decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s.encode("ascii"))

def _parse_color_hex(s: str, default=(255, 255, 255)):
    z = (s or "").strip()
    if z.startswith("#"): z = z[1:]
    try:
        if len(z) == 3:
            return (int(z[0]*2, 16), int(z[1]*2, 16), int(z[2]*2, 16))
        if len(z) == 6:
            return (int(z[0:2], 16), int(z[2:4], 16), int(z[4:6], 16))
    except Exception:
        pass
    return default

def _load_font(size: int):
    for fp in [
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "Arial Unicode.ttf",
    ]:
        try:
            return ImageFont.truetype(fp, size=size)
        except Exception:
            continue
    return ImageFont.load_default()

def _render_text_thumb(token: str) -> bytes:
    # Cache-Key
    cache = _thumb_path_key(f"text|{token}|{TARGET_W}x{TARGET_H}")
    if os.path.isfile(cache):
        return open(cache, "rb").read()

    try:
        cfg = json.loads(_b64url_decode(token).decode("utf-8"))
    except Exception:
        img = Image.new("RGB", (TARGET_W, TARGET_H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        f = _load_font(22)
        d.text((TARGET_W//2, TARGET_H//2), "TXT", fill=(255,255,255), font=f, anchor="mm")
        bio = BytesIO(); img.save(bio, "JPEG", quality=80); bio.seek(0)
        data = bio.getvalue()
        open(cache, "wb").write(data)
        return data

    text = str(cfg.get("text") or "")
    fg = _parse_color_hex(cfg.get("color") or "#ffffff", (255,255,255))
    bg = _parse_color_hex(cfg.get("bg") or "#000000", (0,0,0))
    font_size = int(cfg.get("font_size") or 24)

    size = min(max(10, font_size), 28)
    font = _load_font(size)
    spacing = max(0, int(size * 0.2))

    img = Image.new("RGB", (TARGET_W, TARGET_H), bg)
    d = ImageDraw.Draw(img)

    try:
        l,t,r,b = d.multiline_textbbox((0,0), text, font=font, spacing=spacing, align="center")
        text_w, text_h = r-l, b-t
        x = (TARGET_W - text_w)//2 - l
        y = (TARGET_H - text_h)//2 - t
        d.multiline_text((x, y), text, fill=fg, font=font, spacing=spacing, align="center")
    except Exception:
        d.text((TARGET_W//2, TARGET_H//2), text[:18], fill=fg, font=font, anchor="mm")

    bio = BytesIO()
    img.save(bio, "JPEG", quality=80)
    bio.seek(0)
    data = bio.getvalue()
    try:
        open(cache, "wb").write(data)
    except Exception:
        pass
    return data

def _render_clock_thumb(variant: str) -> bytes:
    """
    Vorschau: Uhrzeit (und optional Datum) auf dunklem Hintergrund.
    Passt die Schriftgrößen dynamisch an, damit alles sicher in 128x128 reinpasst.
    Varianten: time | time_date | time_date_weather
    """
    v = (variant or "time").strip().lower()
    bool_date = ("date" in v)

    worst_time = "88:88"
    worst_date = "28.08.2028"

    from datetime import datetime
    now = datetime.now()
    txt_time = now.strftime("%H:%M")
    txt_date = now.strftime("%d.%m.%Y") if bool_date else None

    img = Image.new("RGB", (TARGET_W, TARGET_H), (0, 0, 0))
    d = ImageDraw.Draw(img)

    PAD = 10
    max_w = TARGET_W - 2 * PAD
    max_h = TARGET_H - 2 * PAD

    size_time = 48
    size_date = 16 if bool_date else 0

    def fits(sz_time: int, sz_date: int) -> bool:
        ft = _load_font(sz_time)
        w_t = d.textbbox((0, 0), worst_time, font=ft)[2]
        h_t = d.textbbox((0, 0), worst_time, font=ft)[3]
        total_h = h_t
        total_w = max_w

        if bool_date:
            fd = _load_font(sz_date)
            w_d = d.textbbox((0, 0), worst_date, font=fd)[2]
            h_d = d.textbbox((0, 0), worst_date, font=fd)[3]
            gap = 6
            total_h = h_t + gap + h_d
            if w_d > max_w:
                return False
        return (w_t <= max_w) and (total_h <= max_h)

    while size_time >= 24:
        size_date_try = size_date
        ok = fits(size_time, size_date_try)
        if ok:
            break
        size_time -= 2
        if bool_date and size_date > 12:
            size_date -= 1

    ft = _load_font(size_time)
    w_t, h_t = d.textbbox((0, 0), txt_time, font=ft)[2:]
    x_t = (TARGET_W - w_t) // 2

    if bool_date:
        fd = _load_font(size_date)
        w_d, h_d = d.textbbox((0, 0), txt_date, font=fd)[2:]
        gap = 6
        total_h = h_t + gap + h_d
        y_start = (TARGET_H - total_h) // 2
        y_t = y_start
        d.text((x_t, y_t), txt_time, fill=(255, 255, 255), font=ft)
        x_d = (TARGET_W - w_d) // 2
        y_d = y_t + h_t + gap
        d.text((x_d, y_d), txt_date, fill=(180, 180, 180), font=fd)
    else:
        y_t = (TARGET_H - h_t) // 2
        d.text((x_t, y_t), txt_time, fill=(255, 255, 255), font=ft)

    bio = BytesIO()
    img.save(bio, "JPEG", quality=80)
    data = bio.getvalue()
    return data



# ----------------------------- ROUTE -----------------------------

@router.get("/api/thumb")
def thumb(file: str = Query(..., description="Pfad relativ zu /media ODER text://<token>")):
    """
    Gibt ein Thumbnail (JPEG) zurück.
    - Bilder: verkleinertes Letterbox 128x128
    - Videos: mittleres Frame (gecacht)
    - NEU: Text-Items (text://<base64url(JSON)>) -> gerendertes Vorschau-JPEG
    """
    if file.startswith("text://"):
        token = file.split("://", 1)[1]
        try:
            data = _render_text_thumb(token)
            return StreamingResponse(BytesIO(data), media_type="image/jpeg")
        except Exception as e:
            log.warning("text thumb fail: %s", e)
            return JSONResponse({"error": "text thumbnail failed"}, status_code=500)

    if file.startswith("clock://"):
        variant = (file.split("://", 1)[1] or "time").lower()
        try:
            data = _render_clock_thumb(variant)
            return StreamingResponse(BytesIO(data), media_type="image/jpeg")
        except Exception as e:
            log.warning("clock thumb fail: %s", e)
            return JSONResponse({"error": "clock thumbnail failed"}, status_code=500)

    src = _safe_media_path(file)
    if not src:
        return JSONResponse({"error": "file not found"}, status_code=404)

    ext = os.path.splitext(src)[1].lower()

    if ext not in VIDEO_EXTS:
        try:
            im = Image.open(src).convert("RGB")
            im.thumbnail((TARGET_W, TARGET_H))
            canvas = Image.new("RGB", (TARGET_W, TARGET_H), (0, 0, 0))
            x0 = (TARGET_W - im.width) // 2
            y0 = (TARGET_H - im.height) // 2
            canvas.paste(im, (x0, y0))
            bio = BytesIO()
            canvas.save(bio, format="JPEG", quality=80)
            bio.seek(0)
            return StreamingResponse(bio, media_type="image/jpeg")
        except Exception as e:
            log.warning("image thumb fail for %s: %s", file, e)
            return RedirectResponse(url=f"/media/{file}")

    tpath = _thumb_path(src, variant="mid")
    if os.path.isfile(tpath):
        return StreamingResponse(open(tpath, "rb"), media_type="image/jpeg")

    try:
        frame = _video_middle_frame(src)  # BGR
        thumb_bgr = _center_letterbox(frame, TARGET_W, TARGET_H)
        jpg = _encode_jpeg(thumb_bgr)
        with open(tpath, "wb") as f:
            f.write(jpg)
        return StreamingResponse(open(tpath, "rb"), media_type="image/jpeg")
    except Exception as e:
        log.warning("thumb (mid) fail for %s: %s", file, e)
        blank = np.zeros((TARGET_H, TARGET_W, 3), dtype=np.uint8)
        try:
            jpg = _encode_jpeg(blank)
            return StreamingResponse(BytesIO(jpg), media_type="image/jpeg")
        except Exception:
            return JSONResponse({"error": "thumbnail failed"}, status_code=500)
