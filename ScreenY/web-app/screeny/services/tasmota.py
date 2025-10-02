import logging, requests
from requests.auth import HTTPBasicAuth

log = logging.getLogger(__name__)

def _url(host: str) -> str:
    host = (host or "").strip()
    if not host.startswith(("http://","https://")):
        host = "http://" + host
    return host.rstrip("/") + "/cm"

def _params(cmnd: str, user: str|None, password: str|None):
    p = {"cmnd": cmnd}
    if user:     p["user"] = user
    if password: p["password"] = password
    return p

def _parse_power_from_json(j: dict|None) -> bool|None:
    if not isinstance(j, dict):
        return None
    for k, v in j.items():
        ks = str(k).upper()
        if ks.startswith("POWER"):
            return str(v).upper() in ("ON", "1", "TRUE")
        if isinstance(v, dict):
            sub = _parse_power_from_json(v)
            if sub is not None:
                return sub
    return None

def _parse_power_response(resp) -> tuple[bool,bool|None]:
    try:
        j = resp.json()
    except Exception:
        j = None
    on = _parse_power_from_json(j)
    if on is not None:
        return True, on
    t = (resp.text or "").strip().upper()
    if t in ("ON","OFF"):
        return True, (t == "ON")
    return True, None

def get_power(host: str, *, user: str|None=None, password: str|None=None, timeout: float=2.0):
    url = _url(host)
    try:
        auth = HTTPBasicAuth(user, password) if (user or password) else None
        r = requests.get(url, params=_params("Power", user, password), timeout=timeout, auth=auth)
        online, on = _parse_power_response(r)
        return {"online": online, "state": ("ON" if on else "OFF") if on is not None else "UNKNOWN"}
    except Exception as e:
        log.info("tasmota get_power offline: %s", e)
        return {"online": False, "state": "UNKNOWN"}

def set_power(host: str, state: str, *, user: str|None=None, password: str|None=None, timeout: float=2.0):
    state = (state or "").strip().upper()
    if state not in ("ON","OFF","TOGGLE"):
        raise ValueError("state must be ON|OFF|TOGGLE")
    url = _url(host)
    try:
        auth = HTTPBasicAuth(user, password) if (user or password) else None
        r = requests.get(url, params=_params(f"Power {state}", user, password), timeout=timeout, auth=auth)
        online, on = _parse_power_response(r)
        return {"ok": True, "online": online, "state": ("ON" if on else "OFF") if on is not None else "UNKNOWN"}
    except Exception as e:
        log.warning("tasmota set_power error: %s", e)
        return {"ok": False, "online": False, "state": "UNKNOWN"}

def get_energy(host: str, *, user: str|None=None, password: str|None=None, timeout: float=2.0):
    """
    Liest Status 8 (StatusSNS) und extrahiert ENERGY.{Power,Voltage,Current,...}.
    Gibt mindestens {"online": True/False, "power_w": <float|None>} zurück.
    """
    url = _url(host)
    try:
        auth = HTTPBasicAuth(user, password) if (user or password) else None
        r = requests.get(url, params=_params("Status 8", user, password), timeout=timeout, auth=auth)
        j = r.json()
        en = None
        if isinstance(j, dict):
            st = j.get("StatusSNS") or j.get("STATUS8") or j  # manche Builds nutzen andere Keys
            if isinstance(st, dict):
                en = st.get("ENERGY")
        data = {
            "online": True,
            "power_w": None,
            "voltage_v": None,
            "current_a": None,
            "apparent_va": None,
            "reactive_var": None,
            "factor": None,
            "total_kwh": None,
            "today_kwh": None,
            "yesterday_kwh": None,
        }
        if isinstance(en, dict):
            data["power_w"]      = en.get("Power")
            data["voltage_v"]    = en.get("Voltage")
            data["current_a"]    = en.get("Current")
            data["apparent_va"]  = en.get("ApparentPower")
            data["reactive_var"] = en.get("ReactivePower")
            data["factor"]       = en.get("Factor")
            data["total_kwh"]    = en.get("Total")
            data["today_kwh"]    = en.get("Today")
            data["yesterday_kwh"]= en.get("Yesterday")
        return data
    except Exception as e:
        log.info("tasmota get_energy offline: %s", e)
        return {"online": False}
