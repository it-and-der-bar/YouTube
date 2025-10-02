import socket, struct, time, logging
import cv2, numpy as np
from typing import Optional, List, Dict, Any
from ..config import BIND_IP, UDP_PORT, BROADCAST_IP, SCREEN_W, SCREEN_H

log = logging.getLogger(__name__)

HDR0, HDR1 = 0x24, 0x24
MSG_REGISTER = 15
MSG_FRAME    = 20
MSG_SYNC     = 100
MSG_CONFIG   = 120
MSG_REG_REQ  = 130
MSG_STATE    = 140
FMT_RGB888   = 10
FMT_JPEG     = 20 
FRAME_CHUNK  = 1440

def hi_lo(v:int): return ((v>>8)&0xFF, v & 0xFF)
def roundup32(n:int):
    size = ((n + 31) // 32) * 32
    return size if size <= FRAME_CHUNK else FRAME_CHUNK
def _hex(b: bytes, n=64) -> str:
    """Erste n Bytes als Hex (mit Leerzeichen)"""
    return " ".join(f"{x:02X}" for x in b[:n])

SYNC_PROFILES = {
    "still":      {"pre": 0.0010, "between": 0.040,  "offsets": (-1, 0, 1)},   # langsam, sicher
    "video3fast": {"pre": 0.0100, "between": 0.0002, "offsets": (-1, 0, 1)},   # dein Setting
    "video1":     {"pre": 0.0000, "between": 0.0000, "offsets": (0,)},         # 1× SYNC
}


class LedBroadcaster:
    def __init__(self):
        self.addr = (BROADCAST_IP, UDP_PORT)
        self._send_lock = None
        self.s = None
        self._open_socket()
        self.fid = 0
        self.modules: List[Dict[str, Any]] = []
        self.tiles: List[Dict[str, Any]] = []   # Layout-Tiles
        self.screen_w = SCREEN_W
        self.screen_h = SCREEN_H

    # ------------------------------------------------
    # Socket & Send
    # ------------------------------------------------
    def _open_socket(self):
        if self.s:
            try: self.s.close()
            except: pass
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.s.bind((BIND_IP, UDP_PORT))
            log.info("UDP :%d bound (broadcast) %s", UDP_PORT, BIND_IP)
        except OSError as e:
            self.s.bind((BIND_IP, 0))
            log.warning("UDP :%d busy, using OS port (%s)", UDP_PORT, e)
        self.s.settimeout(None)

    def send(self, b: bytes):
        if not self._send_lock:
            import threading
            self._send_lock = threading.Lock()
        with self._send_lock:
            self.s.sendto(b, self.addr)

    # ------------------------------------------------
    # Discover / Registry
    # ------------------------------------------------
    def registry_request(self):
        self.send(bytes([HDR0, HDR1, MSG_REG_REQ, 0, 0]))

    def discover(self, seconds: float = 2.0):
        self.modules.clear()
        t_end = time.time() + seconds
        self.s.settimeout(0.2)
        try:
            while time.time() < t_end:
                try:
                    data, addr = self.s.recvfrom(2048)
                except socket.timeout:
                    continue
                if len(data) < 7 or data[0] != HDR0 or data[1] != HDR1 or data[2] != MSG_REGISTER:
                    continue
                mac32 = (data[3]<<24)|(data[4]<<16)|(data[5]<<8)|data[6]
                if any(m.get("mac32")==mac32 for m in self.modules):
                    continue
                m = {
                    "mac32": mac32,
                    "mac16": mac32 & 0xFFFF,
                    "ip": addr[0],
                    "hw": (bytes(data[7:11]).decode("ascii","ignore") if len(data)>=11 else None),
                    "width": (data[11]*16 if len(data)>=13 else None),
                    "height": (data[12]*16 if len(data)>=13 else None),
                }
                self.modules.append(m)
        finally:
            self.s.settimeout(None)
        return self.modules

    # ------------------------------------------------
    # Config & Layout
    # ------------------------------------------------
    def send_config_layout(self, *, grid_cols:int, grid_rows:int, panel_w:int, panel_h:int, tiles:list, line_nums=(0,32)):
        if not tiles:
            raise RuntimeError("tiles required for send_config_layout")

        total_w = int(grid_cols) * int(panel_w)
        total_h = int(grid_rows) * int(panel_h)
        def _key(t): return (t.get("nblock", 0) or 0, t.get("offy", 0), t.get("offx", 0))
        #ordered = sorted(tiles, key=_key)
        ordered = sorted(tiles, key=lambda t: (int(t.get("offy",0)),
                                    int(t.get("offx",0))))

        totW16, totH16 = (total_w // 16) & 0xFF, (total_h // 16) & 0xFF

        for ln in line_nums:  # wie Original: 0 und 32
            payload = bytearray([HDR0, HDR1, MSG_CONFIG, 2,
                                ln & 0xFF, totW16, totH16, len(ordered) & 0xFF])
            
            for idx, t in enumerate(ordered, start=1):
                mac16 = int(t["mac16"]) & 0xFFFF
                w  = int(t.get("w",  panel_w))
                h  = int(t.get("h",  panel_h))
                ox = int(t.get("offx", 0))
                oy = int(t.get("offy", 0))
                payload += bytes([
                    (mac16 >> 8) & 0xFF, mac16 & 0xFF,
                    1, 
                    (w  // 16) & 0xFF,
                    (h  // 16) & 0xFF,
                    (ox // 16) & 0xFF, 
                    (oy // 16) & 0xFF
                ])

            self.send(payload)
        self.send_gamma_identity()
        self.send(bytes([HDR0, HDR1, MSG_STATE, 1, 100]))

        self.screen_w, self.screen_h = total_w, total_h
        self.tiles = ordered

    def send_gamma_identity(self):
        lut = bytes(range(256))
        self.send(bytes([HDR0, HDR1, 127, 0xFF]) + lut)

    # ------------------------------------------------
    # Frame Handling
    # ------------------------------------------------
    def image_to_blob_n(self, img: np.ndarray) -> bytes:
        if not self.tiles:
            raise RuntimeError("no layout/tiles set")
        block_h = 32
        parts = []
        for t in self.tiles:
            x, y = int(t.get("offx",0)), int(t.get("offy",0))
            w, h = int(t.get("w", self.screen_w)), int(t.get("h", self.screen_h))
            tile = img[y:y+h, x:x+w, :]
            for by in range(0, h, block_h):
                block = tile[by:by+block_h, :, :]
                parts.append(block.tobytes())
        return b"".join(parts)
    def image_to_blob(self, img_bgr: np.ndarray) -> bytes:
        import numpy as np
        return np.ascontiguousarray(img_bgr).tobytes()
    def send_frame(self, frame_bgr: np.ndarray, sync_profile="still", mode="fit"): 
        """Sende ein Frame über alle Panels.
        mode = "fill" -> strecken auf volle Fläche (Seitenverhältnis kann verzerren)
        mode = "fit"  -> Bild vollständig zeigen, schwarze Ränder falls nötig
        """
        H, W = frame_bgr.shape[:2]
        target_w, target_h = self.screen_w, self.screen_h

        if (W, H) != (target_w, target_h):
            if mode == "fit":
                # Skalieren mit Erhalt des Seitenverhältnisses
                scale = min(target_w / W, target_h / H)
                new_w, new_h = int(W * scale), int(H * scale)
                resized = cv2.resize(frame_bgr, (new_w, new_h),
                                     interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)

                # In schwarze Fläche zentrieren
                canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
                x0 = (target_w - new_w) // 2
                y0 = (target_h - new_h) // 2
                canvas[y0:y0+new_h, x0:x0+new_w] = resized
                frame_bgr = canvas

            else:  # "fill" oder default
                scale = max(target_w / W, target_h / H)
                new_w, new_h = int(W * scale), int(H * scale)
                resized = cv2.resize(frame_bgr, (new_w, new_h),
                                    interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
                x0 = max(0, (new_w - target_w) // 2)
                y0 = max(0, (new_h - target_h) // 2)
                frame_bgr = resized[y0:y0+target_h, x0:x0+target_w]

        blob = self.image_to_blob(frame_bgr)
        self.frame_bgr_blob(blob, last_units=True, sync_profile=sync_profile)


    def frame_bgr_blob(self, blob: bytes, *, last_units=True, sync_profile: str = "video1"):
        import logging
        log = logging.getLogger(__name__)

        expected = self.screen_w * self.screen_h * 3
        assert len(blob) == expected, f"blob size mismatch: {len(blob)} != {expected}"

        self.fid = (self.fid + 1) & 0xFF
        fid = self.fid
        size = len(blob)

        full = size // FRAME_CHUNK
        rem  = size - full * FRAME_CHUNK
        total = full + (1 if rem > 0 else 0)
        totH, totL = hi_lo(total)

        off = 0
        for idx in range(total):
            last = (idx == total - 1)
            if last:
                part = rem if rem > 0 else FRAME_CHUNK
                padded = roundup32(part)
            else:
                part = FRAME_CHUNK
                padded = FRAME_CHUNK

            chunk = bytearray(padded)
            chunk[:part] = blob[off:off + part]
            off += part

            pkH, pkL = hi_lo(idx)
            size_field = max(1, min(45, (part + 31) // 32)) if (last and last_units) else 45

            hdr = bytes([
                HDR0, HDR1, MSG_FRAME, fid, FMT_RGB888,
                pkH, pkL, totH, totL, size_field
            ])
            log.debug("[DUMP] FRAME0 HDR %s", _hex(hdr, 10))
            log.debug("[DUMP] FRAME0 PAY %s", _hex(chunk, 64))
            self.send(hdr + chunk)

            log.debug(
                f"frame {fid:02d} chunk {idx+1}/{total} "
                f"part={part} padded={padded} "
                f"size_field={size_field} off={off}/{size}"
            )

        if off != size:
            log.warning(
                f"frame {fid:02d} size mismatch after split: sent={off} expected={size}"
            )

        self._send_sync(fid, profile=sync_profile)
        log.debug(f"frame {fid:02d} sync sent ({sync_profile}), total={size} bytes")


    def _send_sync(self, fid:int, profile:str="video1"):
        p = SYNC_PROFILES.get(profile, SYNC_PROFILES["video1"])
        if p["pre"] > 0: time.sleep(p["pre"])
        for i, off in enumerate(p["offsets"]):
            self.send(bytes([HDR0,HDR1,MSG_SYNC, (fid+off)&0xFF]))
            if i < len(p["offsets"]) - 1 and p["between"] > 0:
                time.sleep(p["between"])

    # ------------------------------------------------
    # High-level helpers
    # ------------------------------------------------
    def clear(self, rgb=(0,0,0), sync_profile="still"):
        r,g,b = rgb
        arr = np.zeros((self.screen_h, self.screen_w, 3), dtype=np.uint8)
        arr[...,0] = b; arr[...,1] = g; arr[...,2] = r
        self.send_frame(arr, sync_profile=sync_profile)

    def send_test_pattern(self, layout:dict):
        cols, rows = int(layout["grid_cols"]), int(layout["grid_rows"])
        pw, ph = int(layout["panel_w"]), int(layout["panel_h"])
        total_w, total_h = cols*pw, rows*ph
        img = np.zeros((total_h, total_w, 3), dtype=np.uint8)
        for i,t in enumerate(layout["tiles"], start=1):
            x,y = int(t["offx"]), int(t["offy"])
            w,h = int(t["w"]), int(t["h"])
            hue = (i*50)%180
            color = cv2.cvtColor(np.uint8([[[hue,200,220]]]), cv2.COLOR_HSV2BGR)[0,0]
            color = tuple(int(c) for c in color)
            cv2.rectangle(img,(x,y),(x+w-1,y+h-1), color, -1)
            cv2.rectangle(img,(x,y),(x+w-1,y+h-1), (255,255,255), 2)
            label = f"#{i} / 0x{int(t['mac16']):04X}"
            now = time.localtime()
            timestr = time.strftime("%H:%M:%S", now)
            cv2.putText(img,label,(x+8,y+40),cv2.FONT_HERSHEY_SIMPLEX,0.4,(0,0,0),3,cv2.LINE_AA)
            cv2.putText(img,label,(x+8,y+40),cv2.FONT_HERSHEY_SIMPLEX,0.4,(255,255,255),1,cv2.LINE_AA)
            cv2.putText(img,timestr,(x+8,y+60),cv2.FONT_HERSHEY_SIMPLEX,0.4,(0,0,0),3,cv2.LINE_AA)
            cv2.putText(img,timestr,(x+8,y+60),cv2.FONT_HERSHEY_SIMPLEX,0.4,(255,255,255),1,cv2.LINE_AA)
        self.send_frame(img, sync_profile="still")

    def play_video(self, path:str, fps_limit=None, mode="fill",
                sync_profile="video3fast", loop=False, should_abort=None):
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        tgt = src_fps if fps_limit is None else min(fps_limit, src_fps)
        period = 1.0/max(1e-6, tgt)
        t_next = time.perf_counter()
        try:
            while True:
                if should_abort and should_abort(): break
                ok, frame = cap.read()
                if not ok:
                    if loop: cap.set(cv2.CAP_PROP_POS_FRAMES,0); continue
                    break
                self.send_frame(frame, mode=mode, sync_profile=sync_profile)
                t_next += period
                delay = t_next - time.perf_counter()
                if delay>0: time.sleep(delay)
                else: t_next = time.perf_counter()
        finally:
            cap.release()

    def play_stream(self, source:str, fps_limit:float|None=25.0, mode="fill",
                    sync_profile="video3fast", should_abort=None, max_seconds=None):
        cap = cv2.VideoCapture(source, cv2.CAP_ANY)
        if not cap.isOpened(): return
        try: cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except: pass
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        tgt_fps = min(fps_limit or (src_fps if src_fps>0 else 25.0), 25.0)
        period = 1.0/max(1e-6,tgt_fps)
        t_next = time.perf_counter()
        t0 = t_next
        while True:
            if (should_abort and should_abort()): break
            if max_seconds and (time.perf_counter()-t0)>=max_seconds: break
            ok, frame = cap.read()
            if not ok: time.sleep(0.05); continue
            self.send_frame(frame, mode=mode, sync_profile=sync_profile)
            t_next += period
            delay = t_next - time.perf_counter()
            if delay>0: time.sleep(delay)
            else: t_next = time.perf_counter()
        cap.release()
