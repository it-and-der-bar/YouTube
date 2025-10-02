
import logging, base64
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from ..utils.layout_store import _load_layout, _save_layout
from ..config import LINE_NUMS, TEMPLATE_DIR


log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)

@router.get("/api/panels/discover")
def api_panels_discover(request: Request):
    led = request.app.state.LED
    led.registry_request()
    led.discover(2.0)
    return JSONResponse([
        {
            "mac32": m.get("mac32"),
            "mac16": m.get("mac16"),
            "hw": m.get("hw"),
            "w16": (m.get("width") or 128)//16,
            "h16": (m.get("height") or 128)//16,
        }
        for m in getattr(led, "modules", [])
    ])

@router.post("/api/panels/save")
async def api_panels_save(payload: dict = Body(...)):
    _save_layout(payload or {})
    return {"status": "ok"}

@router.get("/api/panels/get")
def api_panels_get():
    return _load_layout() or {}

@router.post("/api/panels/send_config")
async def api_panels_send_config(payload: dict = Body(...), request: Request = None):
    led = request.app.state.LED
    dest_ip = payload.get("dest_ip") or ""
    if dest_ip:
        led.set_destination(dest_ip)
    led.send_config_layout(
        grid_cols=int(payload.get("grid_cols", 1)),
        grid_rows=int(payload.get("grid_rows", 1)),
        panel_w=int(payload.get("panel_w", 128)),
        panel_h=int(payload.get("panel_h", 128)),
        tiles=payload.get("tiles") or [],
        line_nums=(LINE_NUMS), 
    )
    try:
        mqtt = getattr(request.app.state, "MQTT", None)
        if mqtt:
            mqtt.publish_panel_info(payload or {})
    except Exception:
        log.exception("publish_panel_info failed")
    led.send_gamma_identity()
    _save_layout(payload)
    return {"status": "ok"}

@router.post("/api/panels/test")
async def api_panels_test(payload: dict = Body(None), request: Request = None):
    led = request.app.state.LED
    layout = payload or _load_layout()
    if not layout:
        return PlainTextResponse("no layout", status_code=400)
    dest_ip = (layout or {}).get("dest_ip") or ""
    if dest_ip:
        led.set_destination(dest_ip)
    led.send_test_pattern(layout)
    return {"status": "ok"}

@router.post("/api/panels/image")
async def api_panels_image(payload: dict = Body(...), request: Request = None):
    import cv2, numpy as np
    led = request.app.state.LED
    layout = payload.get("layout") or _load_layout()
    if not layout:
        return PlainTextResponse("no layout", status_code=400)
    b64 = payload.get("image")
    if not b64:
        return PlainTextResponse("no image", status_code=400)
    raw = base64.b64decode(b64.split(",")[-1])
    arr = np.frombuffer(raw, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return PlainTextResponse("decode failed", status_code=400)
    total_w = layout["grid_cols"] * layout["panel_w"]
    total_h = layout["grid_rows"] * layout["panel_h"]
    img_resized = cv2.resize(img, (total_w, total_h), interpolation=cv2.INTER_AREA)
    led.screen_w = total_w
    led.screen_h = total_h
    led.tiles = layout.get("tiles", [])
    if not led.tiles:
        return PlainTextResponse("no tiles in layout", status_code=400)
    led.send_frame(img_resized, sync_profile="still")
    return {"status": "ok"}

# ------------------ Text-Stream (NEU) ------------------

@router.post("/api/text/stream_url")
def api_text_stream_url(payload: dict = Body(...)):
    """
    Liefert eine sichere MJPEG-Stream-URL für Text (UTF/Emojis ok).
    Body:
      text (str, Pflicht), color (hex), bg (hex), font_size (int), speed_px_s (int)
    Antwort:
      {"url": "/stream/text?token=..."}
    """
    import base64, json, time
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    cfg = {
        "text": text,
        "color": (payload.get("color") or "#ffffff").strip(),
        "bg": (payload.get("bg") or "#000000").strip(),
        "font_size": int(payload.get("font_size") or 24),
        "speed_px_s": int(payload.get("speed_px_s") or 40),
        "ts": int(time.time())
    }
    raw = json.dumps(cfg, ensure_ascii=False).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii")
    return {"url": f"/stream/text?token={token}"}


@router.get("/stream/text")
def stream_text(token: str, request: Request):
    """
    MJPEG-Stream mit Text (scrollt automatisch, wenn breiter als Bildschirm).
    Kann als 'file' in der Playlist benutzt werden (is_stream=True).
    """
    from fastapi.responses import StreamingResponse
    import base64, json, time
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    import cv2

    # Token decodieren
    try:
        cfg = json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "bad token"}, status_code=400)

    text       = str(cfg.get("text") or "")
    color_str  = str(cfg.get("color") or "#ffffff")
    bg_str     = str(cfg.get("bg") or "#000000")
    font_size  = int(cfg.get("font_size") or 24)
    speed_px_s = int(cfg.get("speed_px_s") or 40)

    # Layout holen (wie bei dir üblich)
    layout = _load_layout() or {}
    grid_cols = int(layout.get("grid_cols") or 1)
    grid_rows = int(layout.get("grid_rows") or 1)
    panel_w   = int(layout.get("panel_w") or 128)
    panel_h   = int(layout.get("panel_h") or 64)
    W = grid_cols * panel_w
    H = grid_rows * panel_h
    if W <= 0 or H <= 0:
        W, H = 128, 64

    def parse_color(s, default=(255,255,255)):
        z = (s or "").strip()
        if z.startswith("#"): z = z[1:]
        try:
            if len(z)==3:  return (int(z[0]*2,16), int(z[1]*2,16), int(z[2]*2,16))
            if len(z)==6:  return (int(z[0:2],16), int(z[2:4],16), int(z[4:6],16))
        except Exception:
            pass
        names = {"white":(255,255,255),"black":(0,0,0),"red":(255,0,0),"green":(0,255,0),
                 "blue":(0,0,255),"yellow":(255,255,0),"cyan":(0,255,255),"magenta":(255,0,255)}
        return names.get((s or "").lower(), default)

    fg = parse_color(color_str, (255,255,255))
    bg = parse_color(bg_str, (0,0,0))

    # Font (Emoji-tauglich soweit möglich)
    CANDIDATES = [
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "Arial Unicode.ttf",
    ]
    font = None
    for fp in CANDIDATES:
        try:
            font = ImageFont.truetype(fp, size=font_size); break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    # Maße
    bbox = font.getbbox(text)
    text_w = bbox[2]-bbox[0]
    text_h = bbox[3]-bbox[1]
    y = max(0, (H - text_h)//2)

    fps = 20
    interval = 1.0 / fps
    speed = max(10, speed_px_s)

    boundary = "frame"
    headers = {"Content-Type": f"multipart/x-mixed-replace; boundary=--{boundary}"}

    def make_frame(x):
        img = Image.new("RGB", (W, H), bg)
        ImageDraw.Draw(img).text((x, y), text, fill=fg, font=font)
        rgb = np.array(img)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            raise RuntimeError("jpeg encode failed")
        return buf.tobytes()

    def gen():
        # passt Text in Breite? dann statisch zentriert, sonst Lauftext
        x = max(0, (W - text_w)//2)
        if text_w > W:
            x = W
        last = time.time()
        while True:
            now = time.time()
            dt = now - last
            if dt < interval:
                time.sleep(interval - dt)
            last = time.time()
            if text_w > W:
                x -= speed * interval
                if x <= -text_w:
                    x = W
            frame = make_frame(int(x))
            yield (f"--{boundary}\r\n"
                   f"Content-Type: image/jpeg\r\n"
                   f"Content-Length: {len(frame)}\r\n\r\n").encode("ascii") + frame + b"\r\n"

    return StreamingResponse(gen(), headers=headers)
