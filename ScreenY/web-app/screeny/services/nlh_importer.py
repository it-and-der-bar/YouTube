# screeny/services/mfg_import.py
import glob
import os, shutil, logging
from typing import List, Dict, Tuple
from ..config import MEDIA_DIR
from .playlists import is_image, is_video

log = logging.getLogger(__name__)

def _first_existing(path1: str, path2: str, base_dir: str) -> str | None:
    """
    Liefert den ersten existierenden Pfad unter folgenden Kandidaten:
    - path1, path2
    - base_dir/path1, base_dir/path2
    """
    cands = []
    for p in (path1, path2):
        if not p:
            continue
        cands.append(p)
        if base_dir and not os.path.isabs(p):
            cands.append(os.path.join(base_dir, p))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None

def _safe_int(s: str, default: int = 0) -> int:
    try:
        return int(float(s.strip()))
    except Exception:
        return default

def import_manufacturer_text(txt: str, *, base_dir: str = "") -> Dict:
    """
    Importiert eine Hersteller-Playlist (Zeilen beginnen mit '>' und enden mit ';').
    Format (typisch):
      >DisplayName,SourcePath,10,1,10,0,0,0,4,0,0,,0,0;

    Wir nutzen:
      col0: Anzeigename/Dateiname (ggf. auch Pfad)
      col1: Pfad (relativ/absolut)
      col2: (falls Zahl) gewünschte Anzeigedauer in Sekunden für Bilder

    Verhalten:
      - Quelle aus col0/col1 auflösen, Datei nach MEDIA_DIR kopieren (overwrite)
      - Items bauen mit file=<zielname>, mode="fill", loop=1
      - duration für Bilder aus col2 (Fallback 10), für Videos 0
    """
    items: List[Dict] = []

    lines = [ln.strip() for ln in txt.splitlines()]
    for raw in lines:
        if not raw or not raw.startswith(">"):
            continue
        if raw.endswith(";"):
            raw = raw[:-1]
        row = raw[1:] 

        cols = row.split(",")
        col0 = (cols[0] if len(cols) > 0 else "").strip()
        col1 = (cols[1] if len(cols) > 1 else "").strip()
        col2 = (cols[2] if len(cols) > 2 else "").strip()

        if not col0 and not col1:
            continue

        src = _first_existing(col0, col1, base_dir)
        if not src:
            log.warning("Import: Quelle nicht gefunden: %r | %r (base=%r)", col0, col1, base_dir)
            continue

        dst_name = os.path.basename(src)
        os.makedirs(MEDIA_DIR, exist_ok=True)
        dst_path = os.path.join(MEDIA_DIR, dst_name)

        try:
            shutil.copy2(src, dst_path)
        except Exception as e:
            log.warning("Import: Kopieren fehlgeschlagen %s -> %s: %s", src, dst_path, e)
            continue

        # Item bauen
        duration = 0
        if is_image(dst_path):
            duration = _safe_int(col2, 10) or 10  # Fallback 10s für Bilder
        elif is_video(dst_path):
            duration = 0 

        items.append({
            "file": dst_name,     
            "mode": "fill",
            "loop": 1,
            "duration": duration
        })

    return {
        "name": "",           
        "mode": "repeat",
        "items": items
    }

def list_candidate_roots() -> List[str]:
    roots: List[str] = []
    if os.name == "nt":
        for d in "DEFGHIJKLMNOPQRSTUVWXYZ":
            path = f"{d}:/"
            if os.path.exists(path):
                roots.append(path)
    else:
        for base in ("/media", "/mnt"):
            if os.path.isdir(base):
                for name in sorted(os.listdir(base)):
                    path = os.path.join(base, name)
                    if os.path.ismount(path) or os.path.isdir(path):
                        roots.append(path)
    return roots

def scan_playlists_in(root: str, recursive: bool = True, limit_per_ext: int = 500) -> List[str]:
    if not root or not os.path.isdir(root):
        return []

    pats = ["*.txt", "*.plt", "*.npl"]
    paths: List[str] = []
    for pat in pats:
        glob_pat = os.path.join(root, "**", pat) if recursive else os.path.join(root, pat)
        try:
            paths += glob.glob(glob_pat, recursive=recursive)[:limit_per_ext]
        except Exception:
            pass

    res: List[str] = []
    for p in paths:
        try:
            head = open(p, "r", encoding="utf-8", errors="ignore").readline().strip()
            if head.startswith(">") and head[1:-1].split(";")[0].strip().isdigit():
                res.append(p)
        except Exception:
            pass
    return sorted(set(res))

def scan_usb_playlists() -> List[str]:
    res: List[str] = []
    for root in list_candidate_roots():
        res += scan_playlists_in(root, recursive=True)
    return sorted(set(res))
