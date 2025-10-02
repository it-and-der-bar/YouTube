# screeny/utils/layout_push.py
import json
import logging
import os
from typing import Any, Dict, Optional
from ..config import PANEL_LAYOUT_FILE

logger = logging.getLogger(__name__)

def send_active_layout(app, *, line_nums) -> bool:
    """
    Lädt das aktuell gespeicherte Layout und pusht es an die Panels.
    Gibt True zurück, wenn gesendet wurde, sonst False.
    """
    try:
        # _load_layout wird bislang dort genutzt, wo auch beim Startup gesendet wird.
        # Import hier lokal, damit es keine Zirkularimporte gibt.
        from screeny.routes.web import _load_layout 

        layout: Optional[Dict[str, Any]] = _load_layout()
        if not layout:
            logger.warning(f"[send_active_layout] kein Layout gefunden {PANEL_LAYOUT_FILE} – nichts gesendet")
            return False

        app.state.LED.send_config_layout(
            grid_cols=layout["grid_cols"],
            grid_rows=layout["grid_rows"],
            panel_w=layout["panel_w"],
            panel_h=layout["panel_h"],
            tiles=layout["tiles"],
            line_nums=line_nums,
        )
        logger.info("[send_active_layout] Layout + Config erfolgreich gesendet")
        return True

    except Exception as e:
        logger.warning("[send_active_layout] Fehler beim Senden des Layouts: %s", e)
        return False

def _save_layout(layout: dict):
    try:
        with open(PANEL_LAYOUT_FILE, "w", encoding="utf-8") as f:
            json.dump(layout, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.exception("save layout failed: %s", e)
        return False

def _load_layout():
    try:
        if os.path.exists(PANEL_LAYOUT_FILE):
            with open(PANEL_LAYOUT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None