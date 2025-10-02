import logging
import os, json, glob, re
from datetime import datetime
from typing import List, Dict, Any
from ..config import MEDIA_DIR, PLAYLIST_DIR

log = logging.getLogger(__name__)


def list_media() -> List[str]:
    return sorted([f for f in os.listdir(MEDIA_DIR) if os.path.isfile(os.path.join(MEDIA_DIR,f))])

def is_image(name: str) -> bool:
    ext = os.path.splitext((name or "").lower())[1]
    return ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"]

def is_video(name:str) -> bool:
    ext = os.path.splitext(name.lower())[1]
    return ext in [".mp4",".mov",".avi",".mkv",".webm"]

def is_stream(name: str) -> bool:
    if not name:
        return False
    s = name.strip().lower()
    return s.startswith(("rtsp://", "rtmp://", "udp://", "http://", "https://"))

def is_video_or_stream(name: str) -> bool:
    return is_video(name) or is_stream(name)

def playlist_path(name:str) -> str:
    return os.path.join(PLAYLIST_DIR, f"{name}.json")

def list_playlists() -> List[str]:
    a = sorted([os.path.splitext(x)[0] for x in os.listdir(PLAYLIST_DIR) if x.endswith(".json")])
    log.debug(f"Playlists: {a}")
    return a

def pl_load(name:str) -> Dict[str,Any]:
    p = playlist_path(name)
    if os.path.exists(p): return json.load(open(p,"r",encoding="utf-8"))
    return {"name": name, "items": []}

def pl_save(name:str, data:Dict[str,Any]):
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    json.dump(data, open(playlist_path(name),"w",encoding="utf-8"), ensure_ascii=False, indent=2)

def export_manufacturer(pl:Dict[str,Any]) -> str:
    lines = []; items = pl.get("items", [])
    lines.append(f">{len(items)};")
    for it in items:
        file = it["file"]; full = os.path.join("media", file).replace("\\","/")
        dur  = int(it.get("duration", 0) or 0); mode = int(it.get("mode", 4))
        core = f"{file},{full},{dur},0,0,30,0,0,{mode},0,0,,0,0;"
        start = it.get("start",""); end = it.get("end",""); loop = int(it.get("loop", 1) or 1)
        if start and end:
            def dt_to_fields(s):
                dt = datetime.fromisoformat(s); return f"{dt.year},{dt.month},{dt.day},{dt.hour},{dt.minute}"
            sch = f"{dt_to_fields(start)},{dt_to_fields(end)},{loop};"; lines.append(">"+core+sch)
        else:
            lines.append(">"+core)
    return "\n".join(lines)