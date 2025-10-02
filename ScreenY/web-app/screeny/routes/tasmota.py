from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse
from ..services.tasmota import get_power, set_power, get_energy

router = APIRouter()


def _tasmota_enabled(app) -> bool:
    tcfg = (getattr(app.state, "cfg", {}) or {}).get("tasmota") or {}
    return bool(tcfg.get("enabled"))

def _tasmota_cfg(app):
    import os
    tcfg = (getattr(app.state, "cfg", {}) or {}).get("tasmota") or {}
    return {
        "host": tcfg.get("host") or os.getenv("TASMOTA_HOST", ""),
        "user": tcfg.get("user") or os.getenv("TASMOTA_USER", "") or None,
        "password": tcfg.get("password") or os.getenv("TASMOTA_PASS", "") or None,
        "timeout": int(tcfg.get("timeout") or os.getenv("TASMOTA_TIMEOUT", "5")),
    }

@router.get("/api/power")
def api_power_status(request: Request):
    if not _tasmota_enabled(request.app):
        return JSONResponse({"error": "tasmota disabled"}, status_code=400)

    cfg = _tasmota_cfg(request.app)
    stat = get_power(
        cfg["host"],
        user=cfg["user"],
        password=cfg["password"],
        timeout=cfg["timeout"],
    )
    return JSONResponse(stat)

@router.post("/api/power")
def api_power_set(request: Request, state: str = Form(...)):
    if not _tasmota_enabled(request.app):
        return JSONResponse({"error": "tasmota disabled"}, status_code=400)

    cfg = _tasmota_cfg(request.app)
    res = set_power(
        cfg["host"],
        state,
        user=cfg["user"],
        password=cfg["password"],
        timeout=cfg["timeout"],
    )
    return JSONResponse(res)

@router.get("/api/energy")
def api_energy(request: Request):
    if not _tasmota_enabled(request.app):
        return JSONResponse({"error": "tasmota disabled"}, status_code=400)

    cfg = _tasmota_cfg(request.app)
    data = get_energy(
        cfg["host"],
        user=cfg["user"],
        password=cfg["password"],
        timeout=cfg["timeout"],
    )
    return JSONResponse(data)
