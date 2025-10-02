# --- Uhr/Datum/Wetter ----

import time
import requests
import math, requests
from PIL import Image, ImageDraw, ImageFont


WX_LAT  = 49.383  
WX_LON  = 12.283
WX_TZ   = "Europe/Berlin"
WX_CITY = "Ruprechtsberg"
WX_REFRESH_MIN = 5

class WeatherCache:
    def __init__(self, lat=WX_LAT, lon=WX_LON, tz=WX_TZ, refresh_min=WX_REFRESH_MIN):
        self.lat, self.lon, self.tz = lat, lon, tz
        self.refresh_s = refresh_min * 60
        self.last_t = 0
        self.data = None

    def _fetch(self):
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={self.lat}&longitude={self.lon}"
               "&current=temperature_2m,weather_code"
               "&daily=temperature_2m_max,temperature_2m_min,weather_code"
               f"&timezone={self.tz}")
        r = requests.get(url, timeout=5); r.raise_for_status()
        j = r.json()
        code = (j.get("current", {}) or {}).get("weather_code")
        cur_temp = (j.get("current", {}) or {}).get("temperature_2m")
        d = j.get("daily", {}) or {}
        tmax = d.get("temperature_2m_max") or []
        tmin = d.get("temperature_2m_min") or []
        dcode = d.get("weather_code") or []
        def wtxt(c):
            m = {0:"Klar",1:"Überw. klar",2:"Wolkig",3:"Bewölkt",45:"Nebel",48:"gefrier. Nebel",
                 51:"Niesel",53:"Niesel",55:"Niesel",56:"gefrier. Niesel",57:"gefrier. Niesel",
                 61:"Regen",63:"Regen",65:"starker Regen",66:"gefrier. Regen",67:"gefrier. Regen",
                 71:"Schnee",73:"Schnee",75:"starker Schnee",77:"Schneekörner",
                 80:"Schauer",81:"Schauer",82:"starker Schauer",
                 85:"Schneeschauer",86:"Schneeschauer",
                 95:"Gewitter",96:"Gewitter/Griesel",99:"Gewitter/Hagel"}
            try: return m.get(int(c), "—")
            except: return "—"
        self.data = {
            "temp": cur_temp, "code": int(code) if code is not None else None, "txt": wtxt(code),
            "today":    {"tmax": (tmax[0] if tmax else None), "tmin": (tmin[0] if tmin else None), "code": (int(dcode[0]) if dcode else None)},
            "tomorrow": {"tmax": (tmax[1] if len(tmax)>1 else None), "tmin": (tmin[1] if len(tmin)>1 else None), "code": (int(dcode[1]) if len(dcode)>1 else None)},
        }
        self.last_t = time.time()

    def get(self):
        if (time.time() - self.last_t) > self.refresh_s or not self.data:
            try: self._fetch()
            except Exception:
                if not self.data:
                    self.data = {"temp": None, "code": None, "txt": "—",
                                 "today":{"tmax":None,"tmin":None,"code":None},
                                 "tomorrow":{"tmax":None,"tmin":None,"code":None}}
        return self.data

def wmo_to_kind(code: int | None) -> str:
    if code is None: return "cloud"
    c = int(code)
    if c == 0: return "sun"
    if c in (1,2): return "sun_cloud"
    if c == 3: return "cloud"
    if c in (45,48): return "fog"
    if c in (51,53,55): return "drizzle"
    if c in (56,57,66,67): return "sleet"
    if c in (61,63,65,80,81,82): return "rain"
    if c in (71,73,75,77,85,86): return "snow"
    if c in (95,96,99): return "thunder"
    return "cloud"

def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    if hasattr(draw, "textbbox"):
        l,t,r,b = draw.textbbox((0,0), text, font=font); return r-l, b-t
    return draw.textsize(text, font=font)

def _draw_weather_icon(dr: ImageDraw.ImageDraw, x: int, y: int, kind: str, size: int = 28):
    Y=(255,204,0); G=(200,200,200); GL=(170,170,170); B=(90,170,255); W=(240,240,255); K=(255,255,0)
    r=size//2; cx,cy=x+r,y+r
    def sun():
        dr.ellipse([cx-r*0.55,cy-r*0.55,cx+r*0.55,cy+r*0.55], fill=Y)
        for i in range(8):
            a=math.pi/4*i; x1=cx+r*0.8*math.cos(a); y1=cy+r*0.8*math.sin(a)
            x2=cx+r*1.2*math.cos(a); y2=cy+r*1.2*math.sin(a); dr.line([x1,y1,x2,y2], fill=Y, width=2)
    def cloud(xo=0,yo=0):
        bx,by=cx+xo,cy+yo; w=r*1.2; h=r*0.9
        dr.ellipse([bx-w*0.9,by-h*0.6,bx-w*0.3,by+h*0.2], fill=G, outline=GL)
        dr.ellipse([bx-w*0.4,by-h*0.8,bx+w*0.2,by+0], fill=G, outline=GL)
        dr.ellipse([bx+0,by-h*0.6,bx+w*0.8,by+h*0.3], fill=G, outline=GL)
        dr.rounded_rectangle([bx-w*1.0,by-0,bx+w*1.0,by+h*0.6], radius=6, fill=G, outline=GL)
    def rain(n=3):
        for i in range(n):
            xx=x+int(size*0.25)+i*int(size*0.2); dr.line([xx,y+int(size*0.65),xx,y+size-2], fill=B, width=2)
    def snow(n=3):
        for i in range(n):
            xx=x+int(size*0.25)+i*int(size*0.2); yy=y+int(size*0.75)
            dr.line([xx-3,yy,xx+3,yy], fill=W, width=1); dr.line([xx,yy-3,xx,yy+3], fill=W, width=1)
            dr.line([xx-2,yy-2,xx+2,yy+2], fill=W, width=1); dr.line([xx-2,yy+2,xx+2,yy-2], fill=W, width=1)
    def fog(): 
        for i in range(3): yy=y+int(size*0.65)+i*5; dr.line([x+2,yy,x+size-2,yy], fill=W, width=2)
    def bolt():
        pts=[(cx-4,cy-2),(cx+0,cy-2),(cx-2,cy+4),(cx+4,cy+4),(cx-2,cy+12),(cx-6,cy+6)]; dr.polygon(pts, fill=K)
    k=kind
    if k=="sun": sun()
    elif k=="sun_cloud": sun(); cloud(int(r*0.2),int(r*0.2))
    elif k=="cloud": cloud()
    elif k=="drizzle": cloud(); rain(2)
    elif k=="rain": cloud(); rain(3)
    elif k=="sleet": cloud(); rain(2); snow(1)
    elif k=="snow": cloud(); snow(3)
    elif k=="fog": cloud(); fog()
    elif k=="thunder": cloud(); bolt()
    else: cloud()

def render_clock_panel(size=(128,128), *, show_seconds=True, show_date=False, weather=None, city=""):
    im = Image.new("RGB", size, (0,0,0)); dr = ImageDraw.Draw(im); w,h=size
    f_big   = ImageFont.load_default(); f_small = ImageFont.load_default()

    now = time.localtime()
    timestr = time.strftime("%H:%M:%S" if show_seconds else "%H:%M", now)
    tw,th = _measure(dr, timestr, f_big)
    y = 2
    dr.text(((w - tw)//2, y), timestr, fill=(255,255,255), font=f_big); y += th + 1
    if show_date:
        datestr = time.strftime("%d.%m.%Y", now)
        dw,dh = _measure(dr, datestr, f_small)
        dr.text(((w - dw)//2, y), datestr, fill=(170,170,170), font=f_small)
        y += dh + 4
    else:
        y += 4

    if weather:
        k_now = wmo_to_kind(weather.get("code"))
        _draw_weather_icon(dr, 2, y, k_now, size=16)
        temp = weather.get("temp"); txt = weather.get("txt") or "—"
        line1 = f"{city}  {int(round(temp))}°C" if temp is not None else f"{city}"
        x_txt = 2 + 30 + 6
        dr.text((x_txt, y+2), line1, fill=(200,220,255), font=f_small)
        dr.text((x_txt, y+2+12), txt,   fill=(180,200,220), font=f_small)

        y2 = y + 2 + 12 + 16
        for label, day in (("Heute", weather.get("today") or {}), ("Morgen", weather.get("tomorrow") or {})):
            _draw_weather_icon(dr, 4, y2-2, wmo_to_kind(day.get("code")), size=16)
            tmin = day.get("tmin"); tmax = day.get("tmax")
            rng  = f"{int(round(tmin))}/{int(round(tmax))}°C" if (tmin is not None and tmax is not None) else "—"
            dr.text((4+18+16, y2), f"{label}: {rng}", fill=(200,220,255), font=f_small)
            y2 += 24
    return im
