# screeny/routes/schedule.py
import logging
from typing import List, Any, Optional
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from ..services.scheduler import TasmotaScheduler

log = logging.getLogger(__name__)
router = APIRouter()

def _sched(request: Request) -> TasmotaScheduler:
    return request.app.state.SCHED

# ---------- read state ----------
@router.get("/api/tasmota/schedule")
def get_schedule(request: Request):
    return _sched(request).list_state()

# ---------- legacy daily on/off ----------
@router.post("/api/tasmota/schedule/daily")
def set_daily(request: Request, payload: dict = Body(...)):
    on = payload.get("on")
    off = payload.get("off")
    _sched(request).set_daily(on, off)
    return _sched(request).list_state()

# ---------- daily rules (multi) ----------
@router.post("/api/tasmota/schedule/daily_rules/set")
def set_daily_rules(request: Request, payload: dict = Body(...)):
    """
    Replace entire rules list.
    payload = { "rules": [ { "time":"06:00", "action":"ON", "days":["weekdays"] }, ... ] }
    Days can be: integers 0..6, or strings like "mon", "tue", "weekdays", "weekends".
    """
    rules = payload.get("rules") or []
    _sched(request).set_daily_rules(rules)
    return _sched(request).list_state()

@router.post("/api/tasmota/schedule/daily_rules/add")
def add_daily_rule(
    request: Request,
    payload: dict = Body(...),
):
    time_hhmm = payload.get("time")
    action = (payload.get("action") or "").upper()
    days = payload.get("days")  # optional list
    if not time_hhmm or action not in ("ON", "OFF"):
        return JSONResponse({"error": "time and action (ON/OFF) required"}, status_code=400)
    rule = _sched(request).add_daily_rule(time_hhmm=time_hhmm, action=action, days=days)
    return {"added": rule.id, "state": _sched(request).list_state()}

@router.delete("/api/tasmota/schedule/daily_rules/{rule_id}")
def delete_daily_rule(request: Request, rule_id: str):
    ok = _sched(request).delete_daily_rule(rule_id)
    return {"deleted": ok, "state": _sched(request).list_state()}

# ---------- one-shot timers ----------
@router.post("/api/tasmota/schedule/timer")
def add_timer(request: Request, payload: dict = Body(...)):
    action = (payload.get("action") or "").upper()
    if action not in ("ON", "OFF"):
        return JSONResponse({"error": "action must be ON or OFF"}, status_code=400)
    if "hours" in payload and payload["hours"] is not None:
        t = _sched(request).add_timer_in(float(payload["hours"]), action)
    elif "at" in payload and payload["at"]:
        t = _sched(request).add_timer_at(str(payload["at"]), action)
    else:
        return JSONResponse({"error": "either hours or at required"}, status_code=400)
    return {"added": t.id, "state": _sched(request).list_state()}

@router.delete("/api/tasmota/schedule/timer/{timer_id}")
def delete_timer(request: Request, timer_id: str):
    ok = _sched(request).delete_timer(timer_id)
    return {"deleted": ok, "state": _sched(request).list_state()}
