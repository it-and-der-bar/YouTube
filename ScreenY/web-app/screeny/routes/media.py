import os, re, time
from typing import List

from fastapi import APIRouter, Request, Query, UploadFile, File, Body
from fastapi.responses import HTMLResponse, JSONResponse
from ..config import MEDIA_DIR
from .web import templates  

router = APIRouter()

# ----- Seite -----
@router.get("/media", response_class=HTMLResponse)
def media_page(request: Request):
    return templates.TemplateResponse("media_manager.html", {"request": request})

# ----- Helpers -----
def _is_thumb_path(name: str) -> bool:
    n = (name or "").replace("\\", "/")
    return n.startswith(".thumbs/") or "/.thumbs/" in n or n.endswith("/.thumbs")

# ----- API: Listing -----
@router.get("/api/media/list")
def api_media_list(
    q: str = Query("", description="Suche"),
    sort: str = Query("name", description="name|mtime|size"),
):
    root = MEDIA_DIR
    items = []
    ql = (q or "").lower()

    for dirpath, _, files in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        if rel_dir.startswith(".thumbs") or "/.thumbs" in rel_dir:
            continue
        for fn in files:
            if fn.startswith("."):  
                continue
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            if _is_thumb_path(rel):
                continue
            path = os.path.join(dirpath, fn)
            try:
                st = os.stat(path)
            except Exception:
                continue
            if ql and ql not in rel.lower():
                continue
            items.append({
                "name": rel.replace("\\", "/"),
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })

    if sort == "mtime":
        items.sort(key=lambda x: x["mtime"], reverse=True)
    elif sort == "size":
        items.sort(key=lambda x: x["size"], reverse=True)
    else:
        items.sort(key=lambda x: x["name"].lower())

    return {"items": items, "total": len(items)}

# ----- API: Upload -----
@router.post("/api/media/upload")
async def api_media_upload(files: List[UploadFile] = File(...)):
    os.makedirs(MEDIA_DIR, exist_ok=True)
    for up in files:
        name = os.path.basename(up.filename)
        if not name or name.startswith("."):
            continue
        target = os.path.join(MEDIA_DIR, name)
        with open(target, "wb") as f:
            f.write(await up.read())
    return {"status": "ok"}

# ----- API: Rename -----
@router.post("/api/media/rename")
async def api_media_rename(payload: dict = Body(...)):
    old_name = (payload.get("old_name") or "").replace("\\", "/")
    new_name = (payload.get("new_name") or "").replace("\\", "/")
    if not old_name or not new_name or _is_thumb_path(old_name) or _is_thumb_path(new_name):
        return JSONResponse({"error": "bad name"}, status_code=400)
    if old_name.startswith("/") or new_name.startswith("/") or old_name.startswith("../") or new_name.startswith("../"):
        return JSONResponse({"error": "bad path"}, status_code=400)

    src = os.path.join(MEDIA_DIR, old_name)
    dst = os.path.join(MEDIA_DIR, new_name)
    os.makedirs(os.path.dirname(dst) or MEDIA_DIR, exist_ok=True)
    if not os.path.isfile(src):
        return JSONResponse({"error": "not found"}, status_code=404)
    os.rename(src, dst)
    return {"status": "ok"}

# ----- API: Delete -----
@router.post("/api/media/delete")
async def api_media_delete(payload: dict = Body(...)):
    names = payload.get("names") or []
    deleted = 0
    for rel in names:
        if not rel or _is_thumb_path(rel):
            continue
        rel = rel.replace("\\", "/")
        if rel.startswith("/") or rel.startswith("../"):
            continue
        path = os.path.join(MEDIA_DIR, rel)
        try:
            if os.path.isfile(path):
                os.remove(path); deleted += 1
        except Exception:
            pass
    return {"status": "ok", "deleted": deleted}