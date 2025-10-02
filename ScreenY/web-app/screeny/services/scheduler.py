import os, json, asyncio, logging, uuid, base64
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:
    ZoneInfo = None
    class ZoneInfoNotFoundError(Exception): ...

from urllib import request as urlrequest, parse as urlparse
from http.client import HTTPResponse

from . import tasmota
from ..config import CONFS_DIR 

log = logging.getLogger(__name__)

# ---------------- time zone ----------------
def _get_tz():
    """Return Europe/Berlin if available, otherwise local system tz."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Europe/Berlin")
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().astimezone().tzinfo

TZ = _get_tz()

# ---------------- paths ----------------
SCHEDULE_FILE = os.path.join(CONFS_DIR, "tasmota_schedule.json")
CONFIG_PATH   = os.path.join(CONFS_DIR, "config.json")

# ---------------- config helpers (JSON) ----------------
def _load_cfg() -> Dict[str, Any]:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("Scheduler: config.json read failed: %s", e)
        return {}

def _tasmota_cfg() -> Dict[str, Any]:
    cfg = _load_cfg()
    return cfg.get("tasmota") or {}

def _tasmota_enabled() -> bool:
    tcfg = _tasmota_cfg()
    return bool(tcfg.get("enabled"))

def _tasmota_params():
    tcfg = _tasmota_cfg()
    host = (tcfg.get("host") or "").strip()
    user = (tcfg.get("user") or "").strip() or None
    pwd  = (tcfg.get("password") or "").strip() or None
    try:
        timeout = int(tcfg.get("timeout") or 5)
    except Exception:
        timeout = 5
    return host, user, pwd, timeout

# ---------------- HTTP fallback to Tasmota ----------------
def _http_power(action: str) -> bool:
    host, user, pwd, _ = _tasmota_params()
    if not host:
        raise RuntimeError("Tasmota host not configured in config.json (tasmota.host)")

    act = action.upper()
    if act not in ("ON", "OFF"):
        raise ValueError("action must be ON or OFF")

    base = host if host.startswith("http://") or host.startswith("https://") else f"http://{host}"
    q = urlparse.urlencode({"cmnd": f"Power {act}"})
    url = f"{base}/cm?{q}"

    req = urlrequest.Request(url)
    if user and pwd:
        token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")

    try:
        with urlrequest.urlopen(req, timeout=5) as resp: 
            code = resp.getcode()
            if code != 200:
                raise RuntimeError(f"Tasmota HTTP {code}")
            return True
    except Exception as e:
        raise RuntimeError(f"Tasmota HTTP error: {e}")

def _call_power(action: str) -> bool:
    """
    Use services/tasmota with JSON-config params, fallback to HTTP if needed.
    """
    act = action.upper()
    if act not in ("ON", "OFF"):
        raise ValueError("action must be ON or OFF")

    if not _tasmota_enabled():
        log.info("Scheduler: Tasmota disabled in config.json, skip power %s", act)
        return False

    host, user, pwd, timeout = _tasmota_params()
    if not host:
        raise RuntimeError("Tasmota host not configured in config.json (tasmota.host)")

    try:
        fn = getattr(tasmota, "set_power", None)
        if callable(fn):
            return bool(fn(host, "on" if act == "ON" else "off", user=user, password=pwd, timeout=timeout))
    except Exception as e:
        log.debug("tasmota.set_power failed (fallback to HTTP): %s", e)

    return _http_power(act)

WEEKDAY_NAME_TO_IDX = {
    "mon": 0, "monday": 0, "mo": 0,
    "tue": 1, "tuesday": 1, "di": 1,
    "wed": 2, "wednesday": 2, "mi": 2,
    "thu": 3, "thursday": 3, "do": 3,
    "fri": 4, "friday": 4, "fr": 4,
    "sat": 5, "saturday": 5, "sa": 5,
    "sun": 6, "sunday": 6, "so": 6,
}

ALL_DAYS = [0,1,2,3,4,5,6]
WEEKDAYS = [0,1,2,3,4]
WEEKENDS = [5,6]

def normalize_days(days: Optional[List[Any]]) -> List[int]:
    if not days:
        return ALL_DAYS[:]
    out: List[int] = []
    for d in days:
        if isinstance(d, int):
            if 0 <= d <= 6:
                out.append(d)
        elif isinstance(d, str):
            s = d.strip().lower()
            if s in ("weekdays", "workdays"):
                out.extend(WEEKDAYS)
            elif s in ("weekends", "weekend"):
                out.extend(WEEKENDS)
            elif s in WEEKDAY_NAME_TO_IDX:
                out.append(WEEKDAY_NAME_TO_IDX[s])
    out = sorted(set(out))
    return out if out else ALL_DAYS[:]

@dataclass
class Timer:
    id: str
    run_at: str  
    action: str  

    def due(self, now: datetime) -> bool:
        try:
            dt = datetime.fromisoformat(self.run_at)
        except Exception:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return now >= dt

@dataclass
class DailyRule:
    id: str
    time: str        # "HH:MM"
    action: str      # "ON" or "OFF"
    days: List[int]  # 0=Mon ... 6=Sun

    def matches_today(self, now: datetime) -> bool:
        return now.weekday() in self.days

@dataclass
class Daily: 
    on: Optional[str] = None
    off: Optional[str] = None

def _now() -> datetime:
    return datetime.now(TZ)

class TasmotaScheduler:
    def __init__(self, path: str = SCHEDULE_FILE):
        self.path = path
        self.daily_legacy = Daily()               
        self.daily_rules: List[DailyRule] = []    
        self.timers: List[Timer] = []             
        self._task: Optional[asyncio.Task] = None
        self._last_daily_ran: Dict[str, date] = {"on": date.min, "off": date.min}
        self._rule_last_run: Dict[str, date] = {}  

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # legacy block
            d = raw.get("daily") or {}
            self.daily_legacy = Daily(d.get("on"), d.get("off"))
            # new rules
            rules_raw = raw.get("daily_rules", [])
            self.daily_rules = []
            for r in rules_raw:
                rid = r.get("id") or str(uuid.uuid4())
                t = str(r.get("time"))
                a = str(r.get("action", "ON")).upper()
                ds = normalize_days(r.get("days"))
                if a not in ("ON", "OFF"):
                    continue
                self.daily_rules.append(DailyRule(id=rid, time=t, action=a, days=ds))
            self.timers = [Timer(**t) for t in raw.get("timers", [])]
            log.info("Scheduler loaded: %d rules, %d timers, legacy=%s",
                     len(self.daily_rules), len(self.timers), self.daily_legacy)
        except FileNotFoundError:
            self.save()
        except Exception as e:
            log.warning("Scheduler load failed: %s", e)

    def save(self):
        data = {
            "daily": asdict(self.daily_legacy),
            "daily_rules": [asdict(r) for r in self.daily_rules],
            "timers": [asdict(t) for t in self.timers],
        }
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def set_daily(self, on: Optional[str], off: Optional[str]):
        self.daily_legacy = Daily(on or None, off or None)
        self.save()

    def set_daily_rules(self, rules: List[Dict[str, Any]]):
        """Replace entire list of daily rules."""
        new_rules: List[DailyRule] = []
        for r in rules:
            rid = r.get("id") or str(uuid.uuid4())
            t = str(r.get("time"))
            a = str(r.get("action", "ON")).upper()
            ds = normalize_days(r.get("days"))
            if a not in ("ON", "OFF"):
                continue
            new_rules.append(DailyRule(id=rid, time=t, action=a, days=ds))
        self.daily_rules = sorted(new_rules, key=lambda r: r.time)
        self.save()

    def add_daily_rule(self, time_hhmm: str, action: str, days: Optional[List[Any]] = None) -> DailyRule:
        rule = DailyRule(
            id=str(uuid.uuid4()),
            time=time_hhmm,
            action=action.upper(),
            days=normalize_days(days),
        )
        if rule.action not in ("ON", "OFF"):
            raise ValueError("action must be ON or OFF")
        self.daily_rules.append(rule)
        self.daily_rules.sort(key=lambda r: r.time)
        self.save()
        return rule

    def delete_daily_rule(self, rule_id: str) -> bool:
        before = len(self.daily_rules)
        self.daily_rules = [r for r in self.daily_rules if r.id != rule_id]
        if len(self.daily_rules) != before:
            self.save()
            return True
        return False

    # ---------------- one-shot timers API ----------------
    def add_timer_in(self, hours: float, action: str) -> Timer:
        dt = _now() + timedelta(hours=hours)
        t = Timer(id=str(uuid.uuid4()), run_at=dt.isoformat(), action=action.upper())
        self.timers.append(t)
        self.timers.sort(key=lambda x: x.run_at)
        self.save()
        return t

    def add_timer_at(self, when_iso: str, action: str) -> Timer:
        try:
            dt = datetime.fromisoformat(when_iso)
        except Exception:
            raise ValueError("invalid ISO datetime")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        t = Timer(id=str(uuid.uuid4()), run_at=dt.isoformat(), action=action.upper())
        self.timers.append(t)
        self.timers.sort(key=lambda x: x.run_at)
        self.save()
        return t

    def delete_timer(self, timer_id: str) -> bool:
        before = len(self.timers)
        self.timers = [t for t in self.timers if t.id != timer_id]
        if len(self.timers) != before:
            self.save()
            return True
        return False

    def list_state(self) -> Dict[str, Any]:
        return {
            "daily": asdict(self.daily_legacy),
            "daily_rules": [asdict(r) for r in self.daily_rules],
            "timers": [asdict(t) for t in self.timers],
        }

    async def start(self):
        if self._task and not self._task.done():
            return
        self.load()
        self._task = asyncio.create_task(self._run(), name="tasmota-scheduler")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def _run(self):
        log.info("TasmotaScheduler loop started (TZ=%s)", TZ)
        while True:
            try:
                now = _now()

                due = [t for t in self.timers if t.due(now)]
                if due:
                    for t in due:
                        log.info("Timer due: %s -> %s", t.run_at, t.action)
                        try:
                            _call_power(t.action)
                        except Exception as e:
                            log.warning("Tasmota power change failed: %s", e)
                    self.timers = [t for t in self.timers if t not in due]
                    self.save()

                for key in ("on", "off"):
                    hhmm = getattr(self.daily_legacy, key)
                    if not hhmm:
                        continue
                    if self._last_daily_ran.get(key) == now.date():
                        continue
                    try:
                        h, m = map(int, hhmm.split(":"))
                        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    except Exception:
                        continue
                    if now >= target:
                        act = "ON" if key == "on" else "OFF"
                        log.info("Daily (legacy) %s -> %s", key, act)
                        try:
                            _call_power(act)
                        except Exception as e:
                            log.warning("Tasmota power change failed: %s", e)
                        self._last_daily_ran[key] = now.date()

                for r in self.daily_rules:
                    last = self._rule_last_run.get(r.id, date.min)
                    if last == now.date():
                        continue
                    if not r.matches_today(now):
                        continue
                    try:
                        h, m = map(int, str(r.time).split(":"))
                        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    except Exception:
                        continue
                    if now >= target:
                        log.info("Daily rule %s %s @ %s", r.id, r.action, r.time)
                        try:
                            _call_power(r.action)
                        except Exception as e:
                            log.warning("Tasmota power change failed: %s", e)
                        self._rule_last_run[r.id] = now.date()

                if now.hour == 0 and now.minute == 0 and now.second < 10:
                    self._last_daily_ran = {"on": date.min, "off": date.min}
                    self._rule_last_run = {}

                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Scheduler loop error: %s", e)
                await asyncio.sleep(5)
