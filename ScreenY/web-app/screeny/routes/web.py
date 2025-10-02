import os, re, posixpath, json, logging, base64
from fastapi import APIRouter, Request, UploadFile, File, Form, Body
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from ..utils.layout_store import _load_layout, _save_layout
from ..services.player import Player
from ..config import LINE_NUMS, TEMPLATE_DIR, MEDIA_DIR, CONFIG_FILE

from ..services.playlists import (
    list_media, list_playlists, pl_save,
)

log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATE_DIR)

# ------------------ interne Helfer ------------------

def normalize_media_field(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip()
    if "://" in s:
        return s
    s = s.replace("\\", "/")
    s = re.sub(r"^[A-Za-z]:/", "", s)
    while s.startswith("/"):
        s = s[1:]
    s = posixpath.normpath(s)
    if s.startswith(".."):
        s = posixpath.basename(s)
    return s

# ------------------ UI / Standardrouten ------------------

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    app = request.app
    cfg = getattr(app.state, "cfg", {"autostart": "", "line_num": 32})
    led = getattr(app.state, "LED", None)
    mods = getattr(led, "modules", []) if led else []
    return templates.TemplateResponse("index.html", {
        "request": request,
        "mods": mods,
        "line_num": cfg.get("line_num", 32),
        "media": list_media(),
        "playlists": list_playlists(),
        "cfg": cfg,
    })


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    name = os.path.basename(file.filename)
    target = os.path.join(MEDIA_DIR, name)
    with open(target, "wb") as f:
        f.write(await file.read())
    return RedirectResponse("/", status_code=303)


