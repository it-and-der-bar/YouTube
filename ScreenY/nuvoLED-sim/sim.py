#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, socket, threading, time, logging, sys, io, tempfile, os
from dataclasses import dataclass
from typing import Dict, Tuple, Optional, List

try:
    import pygame
    from PIL import Image
    import numpy as np
except Exception as e:
    print("Missing dependency:", e)
    print("pip install pygame pillow numpy")
    sys.exit(1)

# ---------- Protocol ----------
HDR0, HDR1 = 0x24, 0x24
MSG_REGISTER     = 15
MSG_FRAME        = 20 
MSG_FRAME_FINISH = 30
MSG_SYNC         = 100
MSG_CONFIG       = 120
MSG_REG_REQ      = 130
MSG_STATE        = 140
MSG_GAMMA        = 127
MSG_A0           = 160

UDP_PORT    = 2000
BCAST_ADDR  = "255.255.255.255"

FRAME_CHUNK = 1440
def roundup32(n:int):
    size = ((n + 31) // 32) * 32
    return size if size <= FRAME_CHUNK else FRAME_CHUNK

# ---------- Logging ----------
log = logging.getLogger("nuvo_sim")
log.setLevel(logging.DEBUG)
h = logging.StreamHandler()
h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
log.addHandler(h)

# ---------- Data ----------
@dataclass
class ModuleDesc:
    mac32: int
    mac16: int
    hw: str = "P4T"
    width: int = 128
    height: int = 128
    offx: int = 0
    offy: int = 0
    numBlock: int = 0
    cfg_seen: int = 0

class FrameAssembly:
    def __init__(self, fid:int, ftype:int, total:int):
        self.fid = fid
        self.ftype = ftype
        self.total = total
        self.parts: Dict[int, bytes] = {}
        self.exp_len: Dict[int, int] = {}
        self.received = 0
        self.default_chunk: Optional[int] = None
        self.seen_finish: bool = False

    def add(self, idx:int, payload:bytes, expected_len:int):
        if idx not in self.parts:
            self.parts[idx] = payload
            self.exp_len[idx] = expected_len
            self.received += 1
            if self.default_chunk is None:
                self.default_chunk = expected_len

# ---------- Simulator ----------
class NuvoSimulator:
    def __init__(
        self,
        bind_ip:str,
        grid:Tuple[int,int],
        panel_size:Tuple[int,int],
        raw_order:str="bgr",
        do_dumps:bool=False,
        dump_dir:Optional[str]=None,
        respect_config_offsets:bool=False,
        apply_lut:bool=True,
        block_h:int=32,
        mac32_base=0,
        mac16_start=1,
        interleave:str="panel",   # 'panel' oder 'row'
        panel_gap:int=1,          # visueller Rahmen zwischen Modulen
        led_gap:int=1,            # Pixel-Gap (rechts & unten) für LED-Look
        led_gap_color:Tuple[int,int,int]=(26,26,26),  # Farbe der Pixel-Gaps
        outer_margin:int=12       # Rand ums Canvas
    ):
        self.bind_ip = bind_ip
        self.grid_x, self.grid_y = grid
        self.panel_w, self.panel_h = panel_size
        self.grid_w = self.grid_x * self.panel_w
        self.grid_h = self.grid_y * self.panel_h

        self.raw_order = raw_order
        self.do_dumps = do_dumps
        self.dump_dir = dump_dir or tempfile.gettempdir()
        self.respect_config_offsets = respect_config_offsets
        self.apply_lut = apply_lut
        self.block_h = block_h
        self.interleave = interleave  # 'panel'|'row'

        # Darstellung
        self.panel_gap = max(0, int(panel_gap))
        self.led_gap = max(0, int(led_gap))
        self.led_gap_color = tuple(int(c) for c in led_gap_color)
        self.outer_margin = max(0, int(outer_margin))

        # Reihenfolge/Adressierung
        self.order_mode = "mac16"  # 'mac16' | 'nblock' | 'config' | 'grid'
        self.place_mode = "layout" # 'layout' (CONFIG-Offsets) oder 'id'
        self.place_map: Dict[int, Tuple[int,int]] = {}  # mac16 -> (dst_offx, dst_offy)

        self.strict = False

        self.sock = None
        self._open_socket()

        # virtuelle Module
        self.modules: List[ModuleDesc] = []
        log.info(f"Setting mac32: {mac32_base} | Setting mac16: {mac16_start} ")
        idx = 0
        for y in range(self.grid_y):
            for x in range(self.grid_x):
                mac16 = (mac16_start + idx)
                mac32 = mac32_base | mac16  # low16 = mac16
                self.modules.append(ModuleDesc(
                    mac32=mac32,
                    mac16=mac16,
                    width=self.panel_w, height=self.panel_h,
                    offx=x*self.panel_w, offy=y*self.panel_h,
                    numBlock=idx
                ))
                idx += 1
        self.panel_by_mac16: Dict[int, ModuleDesc] = {m.mac16: m for m in self.modules}

        # dynamik
        self.last_cfg_sig: Optional[bytes] = None
        self.total_w = self.grid_w
        self.total_h = self.grid_h
        self.assemblies: Dict[int, FrameAssembly] = {}
        self.last_canvas = Image.new("RGB", (self.grid_w, self.grid_h), (0,0,0))
        self.running = True

        # Strict-Flags/State
        self._cfg_last_l0: Optional[bytes] = None
        self._cfg_last_l32: Optional[bytes] = None
        self._cfg_ready_ts: float = 0.0
        self.display_enabled: bool = True
        self.expected_blocks: Optional[int] = None
        self.bytes_per_block: int = self.panel_w * 32 * 3  # wird ggf. angepasst
        # LUT (Gamma)
        self.lut = np.arange(256, dtype=np.uint8)  # identity

        # debug state
        self.show_ids   = False
        self.show_stats = True
        self.show_grid  = True
        self.last_frame_id   = None
        self.last_frame_type = None  # 10=RAW888,20=JPEG,30=RGB565
        self.last_frame_bytes= 0
        self.last_frame_time = 0.0

        # komplette CONFIG-Sequenz (auch fremde Panels)
        self.cfg_entries: List[dict] = []

        self._last_target_mac16: Optional[int] = None

        # Ziel-Highlights (mac16 -> (color, until_ts))
        self._marks: Dict[int, Tuple[Tuple[int,int,int], float]] = {}

        # Thread-Lock (last_canvas/assemblies)
        self._lock = threading.Lock()

        log.info("UDP recv socket bound to %r:%d", self.bind_ip, UDP_PORT)
        log.info("Created %d panels (%dx%d), each %dx%d",
                 self.grid_x*self.grid_y, self.grid_x, self.grid_y, self.panel_w, self.panel_h)

        # periodisch REGISTER via Broadcast
        self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
        self._announce_thread.start()

    # ---------- Strict defaults ----------
    def _apply_strict_defaults(self):
        if not self.strict:
            return
        self.raw_order  = "bgr"
        self.block_h    = 32
        self.interleave = "panel"
        self.order_mode = "config"
        self.place_mode = "layout"
        self.bytes_per_block = self.panel_w * self.block_h * 3
        log.info("[STRICT] defaults: raw_order=BGR block_h=32 interleave=panel order=config place=layout")

    # ---------- UDP ----------
    def _open_socket(self):
        if self.sock:
            try: self.sock.close()
            except: pass
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind((self.bind_ip, UDP_PORT))

    def _announce_loop(self):
        while self.running:
            try:
                self._send_register_broadcast()
            except Exception as e:
                log.debug("announce error: %s", e)
            time.sleep(1.0)

    def _send_register_broadcast(self):
        for m in self.modules:
            pkt = bytearray([HDR0,HDR1,MSG_REGISTER])
            pkt.extend([(m.mac32>>24)&0xFF, (m.mac32>>16)&0xFF, (m.mac32>>8)&0xFF, m.mac32&0xFF])
            hw = m.hw[:4].ljust(4, "\x00").encode("ascii","ignore")
            pkt.extend(hw)
            pkt.append(m.width//16)
            pkt.append(m.height//16)
            self.sock.sendto(bytes(pkt), (BCAST_ADDR, UDP_PORT))

    # ---------- helpers ----------
    @staticmethod
    def _sizepack_to_bytes(size_field:int) -> int:
        return size_field if size_field >= 128 else size_field*32

    @staticmethod
    def _strip_jpeg_padding(blob: bytes) -> bytes:
        eoi = blob.rfind(b"\xFF\xD9")
        return blob if eoi == -1 else blob[:eoi+2]

    def _dump(self, name: str, data: bytes):
        if not self.do_dumps:
            return
        try:
            fn = os.path.join(self.dump_dir, name)
            with open(fn, "wb") as f: f.write(data)
            log.info("[DUMP] %s", fn)
        except Exception as e:
            log.debug("dump error: %s", e)

    # ---------- order & placement ----------
    def _ordered_panels(self) -> List[ModuleDesc]:
        panels = getattr(self, "configured_order", None) or list(self.modules)
        if self.order_mode == "mac16":
            return sorted(panels, key=lambda m: m.mac16)
        if self.order_mode == "nblock":
            return sorted(panels, key=lambda m: m.numBlock)
        if self.order_mode == "grid":
            return sorted(panels, key=lambda m: (m.offy, m.offx))
        return panels  # 'config'

    def _compute_placement(self):
        self.place_map.clear()

        if not getattr(self, "cfg_entries", None):
            for m in self.modules:
                self.place_map[m.mac16] = (m.offx, m.offy)
            return

        if self.place_mode == "layout":
            for e in self.cfg_entries:
                if e.get("module") is not None:
                    self.place_map[e["mac16"]] = (e["offx"], e["offy"])
            return

        # place_mode == 'id'
        def _key(e):
            if self.order_mode == "mac16":  return (e["mac16"],)
            if self.order_mode == "nblock": return (e["nBlock"],)
            if self.order_mode == "grid":   return (e["offy"], e["offx"])
            return (self.cfg_entries.index(e),)

        my_entries = sorted([e for e in self.cfg_entries if e["mine"]], key=_key)
        gx, gy = self.grid_x, self.grid_y
        for i, e in enumerate(my_entries):
            cx = (i % gx) * self.panel_w
            cy = (i // gx) * self.panel_h
            self.place_map[e["mac16"]] = (cx, cy)

    def _dst_xy(self, mac16:int, fallback_x:int, fallback_y:int) -> Tuple[int,int]:
        return self.place_map.get(mac16, (fallback_x, fallback_y))

    # ---------- handlers ----------
    def handle_config(self, data: bytes):
        if len(data) < 8: return

        colorType = data[3]
        lineNum   = data[4]
        totW16    = data[5]
        totH16    = data[6]
        numBlocks = data[7]

        self.total_w = max(1, totW16*16)
        self.total_h = max(1, totH16*16)

        log.info("[CFG] colorType=%d lineNum=%d total=%dx%d blocks=%d",
                 colorType, lineNum, self.total_w, self.total_h, numBlocks)

        # Strict: doppelte CONFIG (0 und 32) sammeln & vergleichen
        if self.strict:
            sig = bytes(data)
            if lineNum == 0:
                self._cfg_last_l0 = sig
            elif lineNum == 32:
                self._cfg_last_l32 = sig
            if self._cfg_last_l0 and self._cfg_last_l32 and self._cfg_last_l0 == self._cfg_last_l32:
                self._cfg_ready_ts = time.time()
            else:
                self._cfg_ready_ts = 0.0

        self.configured_order: List[ModuleDesc] = []
        self.cfg_entries = []
        i = 8
        for _ in range(numBlocks):
            if i+6 >= len(data): break
            mac16   = (data[i]<<8) | data[i+1]
            nBlock  = data[i+2]
            w       = data[i+3]*16
            h       = data[i+4]*16
            offx    = data[i+5]*16
            offy    = data[i+6]*16
            i += 7

            m = self.panel_by_mac16.get(mac16)
            mine = m is not None
            if mine:
                m.width, m.height = w, h
                if self.respect_config_offsets:
                    m.offx, m.offy = offx, offy
                m.numBlock = nBlock
                m.cfg_seen += 1
                self.configured_order.append(m)

            self.cfg_entries.append({
                "mac16": mac16, "nBlock": nBlock,
                "w": w, "h": h, "offx": offx, "offy": offy,
                "mine": mine, "module": m,
            })

        self.configured_order.sort(key=lambda m: m.numBlock)

        total_cfg = len(self.cfg_entries)
        mine_cfg  = sum(1 for e in self.cfg_entries if e["mine"])
        log.info("[CFG] panels(all)=%d  panels(mine)=%d (order=%s, place=%s)",
                 total_cfg, mine_cfg, self.order_mode, self.place_mode)
        for e in self.cfg_entries:
            tag = "mine" if e["mine"] else "other"
            log.info("  %s mac16=%4d(0x%04X) nb=%3d off=(%d,%d) size=%dx%d",
                     tag, e["mac16"], e["mac16"], e["nBlock"], e["offx"], e["offy"], e["w"], e["h"])

        # Anzeige-Placement berechnen
        self._compute_placement()

        # Strict: erwartete RAW-Blöcke (über alle Tiles) vorberechnen
        if self.strict:
            bh = self.block_h
            self.bytes_per_block = self.panel_w * bh * 3
            ok = True
            blocks = 0
            for e in self.cfg_entries:
                pw, ph = e["w"] or self.panel_w, e["h"] or self.panel_h
                if pw != self.panel_w or (ph % bh) != 0:
                    ok = False
                    break
                blocks += ph // bh
            self.expected_blocks = (blocks if ok else None)

        with self._lock:
            if self.last_canvas.size != (self.grid_w, self.grid_h):
                self.last_canvas = Image.new("RGB", (self.grid_w, self.grid_h), (0,0,0))

    def handle_frame_packet(self, data: bytes):
        if len(data) < 10:
            return

        fid   = data[3]
        ftype = data[4]
        pack  = (data[5]<<8)|data[6]
        total = (data[7]<<8)|data[8]
        sizeF = data[9]

        if self.strict:
            if ftype not in (10, 20, 30): return
            if total <= 0 or total > 4096: return
            if pack >= total: return

        p0 = 10
        claimed = self._sizepack_to_bytes(sizeF)
        avail   = len(data) - p0
        is_last = (pack == total - 1)

        if self.strict:
            if sizeF < 1 or sizeF > 45: return
            if not is_last and claimed != FRAME_CHUNK: return
            if avail < claimed: return
            payload = data[p0:p0+claimed]
        else:
            payload = data[p0 : p0 + max(0, min(claimed, avail))]

        asm = self.assemblies.get(fid)
        if asm is None:
            asm = FrameAssembly(fid, ftype, total)
            self.assemblies[fid] = asm
        else:
            if self.strict and (asm.ftype != ftype or asm.total != total):
                return

        if self.strict and pack in asm.parts:
            return

        asm.add(pack, payload, claimed)

    def handle_frame_finish(self, data: bytes):
        if len(data) < 4:
            return
        fid = data[3]
        asm = self.assemblies.get(fid)
        if asm:
            asm.seen_finish = True

    def _apply_lut_img(self, img: Image.Image) -> Image.Image:
        if not self.apply_lut:
            return img
        arr = np.asarray(img)
        arr = self.lut[arr]
        return Image.fromarray(arr, "RGB")

    def _paste_single_raw888(self, blob: bytes):
        bytes_one = self.panel_w*self.panel_h*3
        if len(blob) < bytes_one:
            return
        arr = np.frombuffer(blob[:bytes_one], dtype=np.uint8).reshape((self.panel_h, self.panel_w, 3))
        if self.raw_order == "bgr":
            arr = arr[..., ::-1]
        tile = Image.fromarray(arr, "RGB")
        tile = self._apply_lut_img(tile)

        panels = self._ordered_panels()
        target = panels[0]
        dx, dy = self._dst_xy(target.mac16, target.offx, target.offy)

        with self._lock:
            canvas = self.last_canvas
            if canvas.size != (self.grid_w, self.grid_h):
                canvas = Image.new("RGB", (self.grid_w, self.grid_h), (0,0,0))
            canvas.paste(tile, (dx, dy))
            self.last_canvas = canvas

    def _raw_deinterleave_panels(self, raw: bytes) -> Image.Image:
        H = self.grid_h; W = self.grid_w
        out = np.zeros((H, W, 3), dtype=np.uint8)

        bh = max(1, self.block_h)
        bytes_per_panel_block = self.panel_w * bh * 3
        if bytes_per_panel_block == 0:
            return Image.fromarray(out, "RGB")

        if self.cfg_entries:
            if self.order_mode == "mac16":
                entries = sorted(self.cfg_entries, key=lambda e: e["mac16"])
            elif self.order_mode == "nblock":
                entries = sorted(self.cfg_entries, key=lambda e: e["nBlock"])
            elif self.order_mode == "grid":
                entries = sorted(self.cfg_entries, key=lambda e: (e["offy"], e["offx"]))
            else:  # 'config'
                entries = list(self.cfg_entries)
            def blocks_for(e): return max(1, (e["h"] or self.panel_h) // bh)
        else:
            mods = self._ordered_panels()
            entries = [{"mac16": m.mac16, "nBlock": m.numBlock, "w": m.width, "h": m.height,
                        "offx": m.offx, "offy": m.offy, "mine": True, "module": m} for m in mods]
            def blocks_for(e): return max(1, (e["h"] or self.panel_h) // bh)

        it = []
        max_blocks = 0
        for e in entries:
            b = blocks_for(e); max_blocks = max(max_blocks, b)
            if self.interleave == "panel":
                for yb in range(b):
                    it.append((e, yb))
        if self.interleave == "row":
            for yb in range(max_blocks):
                for e in entries:
                    if yb < blocks_for(e):
                        it.append((e, yb))

        idx_block = 0
        blocks_available = len(raw) // bytes_per_panel_block

        for (e, yb) in it:
            if idx_block >= blocks_available:
                break
            chunk = raw[idx_block*bytes_per_panel_block : (idx_block+1)*bytes_per_panel_block]
            idx_block += 1

            if not e["mine"]:
                continue

            fallback_x, fallback_y = e["offx"], e["offy"]
            dx, dy = self._dst_xy(e["mac16"], fallback_x, fallback_y)

            y0 = dy + yb * bh
            block = np.frombuffer(chunk, dtype=np.uint8).reshape((bh, self.panel_w, 3))
            if self.raw_order == "bgr":
                block = block[..., ::-1]
            y1 = min(y0+bh, H)
            x1 = min(dx+self.panel_w, W)
            out[y0:y1, dx:x1, :] = block[:(y1-y0), :(x1-dx), :]

        img = Image.fromarray(out, "RGB")
        return self._apply_lut_img(img)
    
    def _raw_fullframe_to_image(self, raw: bytes) -> Image.Image:
        W = self.total_w or self.grid_w
        H = self.total_h or self.grid_h
        need = W * H * 3
        if len(raw) < need:
            raw = raw + b"\x00" * (need - len(raw))
        arr = np.frombuffer(raw[:need], dtype=np.uint8).reshape((H, W, 3))
        if self.raw_order == "bgr":
            arr = arr[..., ::-1]
        img = Image.fromarray(arr, "RGB")
        return self._apply_lut_img(img)

    # ---------- selection / gamma ----------
    def _mark(self, mac16:int, color:Tuple[int,int,int], duration:float):
        self._marks[mac16] = (color, time.time() + duration)

    def handle_sync(self, data: bytes, addr):
        if len(data) < 4:
            return
        fid = data[3]
        asm = self.assemblies.get(fid)
        if not asm:
            return

        if self.strict:
            if not self.display_enabled:
                return
            if not self._cfg_ready_ts:
                return
            if asm.received != asm.total:
                return
            if not asm.seen_finish:
                return

        total = asm.total
        out = bytearray()
        for i in range(total):
            exp = asm.exp_len.get(i)
            part = asm.parts.get(i)
            if part is None:
                if not self.strict:
                    pad_len = exp if exp is not None else (asm.default_chunk or FRAME_CHUNK)
                    out.extend(b"\x00" * pad_len)
                    continue
                return
            if exp is not None and len(part) != exp and self.strict:
                return
            out.extend(part)

        raw = bytes(out)
        ftype = asm.ftype
        img = None

        try:
            if ftype == 20:  # JPEG
                if self.strict:
                    if not (len(raw) >= 4 and raw[:2] == b"\xFF\xD8" and raw.rfind(b"\xFF\xD9") != -1):
                        return
                    img = Image.open(io.BytesIO(raw)).convert("RGB")
                    img = self._apply_lut_img(img)
                else:
                    def is_jpeg(b: bytes) -> bool:
                        return len(b) >= 4 and b[:2] == b"\xFF\xD8" and b.rfind(b"\xFF\xD9") != -1
                    if is_jpeg(raw):
                        jpg = self._strip_jpeg_padding(raw)
                        img = Image.open(io.BytesIO(jpg)).convert("RGB")
                        img = self._apply_lut_img(img)
                    if img is None:
                        img = self._raw_deinterleave_panels(raw)

            elif ftype == 10: 
                full_need = (self.total_w or self.grid_w) * (self.total_h or self.grid_h) * 3
                if len(raw) == full_need:
                    img = self._raw_fullframe_to_image(raw)
                else:
                    if self.strict:
                        if self.expected_blocks is None:
                            return
                        expected = self.expected_blocks * self.bytes_per_block
                        if len(raw) != expected:
                            return
                    img = self._raw_deinterleave_panels(raw)


            elif ftype == 30:
                return

            if img is None:
                if not self.strict:
                    self._dump(f"nuvo_frame_{fid}_dump.bin", raw)
                return

            if img.size != (self.grid_w, self.grid_h):
                canvas = Image.new("RGB", (self.grid_w, self.grid_h))
                canvas.paste(img.crop((0,0,self.grid_w,self.grid_h)), (0,0))
                img = canvas

            with self._lock:
                self.last_canvas = img
                self.last_frame_id    = fid
                self.last_frame_type  = ftype
                self.last_frame_bytes = len(raw)
                self.last_frame_time  = time.time()
        except Exception:
            if not self.strict:
                self._dump(f"nuvo_frame_{fid}_dump.bin", raw)
            return
        finally:
            try: del self.assemblies[fid]
            except: pass

    def _handle_gamma(self, data: bytes):
        now = time.time()
        if len(data) < 3:
            return

        body = data[3:]

        if len(body) >= 257 and body[0] == 0xFF:
            lut = np.frombuffer(body[1:1+256], dtype=np.uint8).copy()
            self.lut = lut
            if self.do_dumps:
                self._dump(f"nuvo_lut_{int(now)}.bin", bytes(lut))
            log.debug("[GAMMA] LUT broadcast (256 bytes)")
            return

        mac16 = None
        code  = None
        lut   = None

        if len(body) >= 3:
            m16 = (body[0] << 8) | body[1]
            c   = body[2]
            if len(body) >= 3+257 and body[3] == 0xFF:
                lut = np.frombuffer(body[4:4+256], dtype=np.uint8).copy()
            mac16, code = m16, c

        if mac16 is None and len(body) >= 5:
            m32 = (body[0]<<24) | (body[1]<<16) | (body[2]<<8) | body[3]
            c   = body[4]
            low16 = m32 & 0xFFFF
            if low16 in self.panel_by_mac16 or (m32 >> 24) in (0x10, 0x11, 0x00):
                mac16, code = low16, c
                if len(body) >= 5+257 and body[5] == 0xFF:
                    lut = np.frombuffer(body[6:6+256], dtype=np.uint8).copy()

        if mac16 is None and len(body) >= 3:
            c   = body[0]
            m16 = (body[1] << 8) | body[2]
            if m16 in self.panel_by_mac16:
                mac16, code = m16, c
                if len(body) >= 3+257 and body[3] == 0xFF:
                    lut = np.frombuffer(body[4:4+256], dtype=np.uint8).copy()

        if mac16 is None or code is None:
            log.debug("[GAMMA] unrecognized targeted payload: len=%d body=%s",
                      len(body), body[:16].hex())
            return

        if lut is not None:
            self.lut = lut
            if self.do_dumps:
                self._dump(f"nuvo_lut_{int(now)}.bin", bytes(lut))

    def _handle_a0(self, data: bytes):
        if len(data) < 4:
            return
        body = data[3:]

        mac16 = None
        code  = None

        if len(body) >= 3:
            mac16 = (body[0] << 8) | body[1]
            code  = body[2]

        if (mac16 is None or mac16 not in self.panel_by_mac16) and len(body) >= 5:
            m32 = (body[0]<<24) | (body[1]<<16) | (body[2]<<8) | body[3]
            mac16 = m32 & 0xFFFF
            code  = body[4]

        if mac16 is None or code is None:
            log.debug("[A0] unrecognized payload len=%d body=%s", len(body), body[:16].hex())
            return

        if mac16 == 0:
            if self._last_target_mac16 in self.panel_by_mac16:
                mac16 = self._last_target_mac16
            else:
                log.debug("[A0] broadcast meta code=0x%02X ignored (no last target)", code)
                return

        self._last_target_mac16 = mac16

        yellot_set = {1, 0x01, 0x11, 0x21, 0xB0}
        green_set  = {0, 0x00, 0x10, 0x20, 0xB1}
        if code in green_set:
            col, dur = (60,210,60), 2.0
        elif code in yellot_set:
            col, dur = (240,200,30), 0.9
        else:
            col, dur = (220,60,60), 2.0

        self._mark(mac16, col, dur)

    # ---------- GUI ----------
    def _make_led_surface(self, img: Image.Image) -> pygame.Surface:
        arr = np.asarray(img, dtype=np.uint8)
        H, W = arr.shape[0], arr.shape[1]
        g = self.led_gap

        if g <= 0:
            return pygame.image.fromstring(img.tobytes(), (W, H), img.mode)

        outW = W*(1+g) - g
        outH = H*(1+g) - g
        out = np.empty((outH, outW, 3), dtype=np.uint8)
        out[:] = self.led_gap_color
        out[0:outH:(g+1), 0:outW:(g+1), :] = arr
        return pygame.image.frombuffer(out.tobytes(), (outW, outH), "RGB")

    def start_gui(self):
        pygame.init()
        with self._lock:
            base_w, base_h = self.last_canvas.size
        min_w = max(base_w, 320)
        win_h = max(base_h + 160, 240)
        win = pygame.display.set_mode((min_w + self.outer_margin*2, win_h))
        pygame.display.set_caption("NuvoLED Simulator")
        try:
            font = pygame.font.SysFont("Consolas", 12)
            tiny = pygame.font.SysFont("Consolas", 10)
        except Exception:
            font = pygame.font.SysFont(None, 12)
            tiny = pygame.font.SysFont(None, 10)
        clock = pygame.time.Clock()

        while self.running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                elif ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_i: self.show_ids = not self.show_ids
                    if ev.key == pygame.K_s: self.show_stats = not self.show_stats
                    if ev.key == pygame.K_g: self.show_grid = not self.show_grid
                    if ev.key == pygame.K_r: self.raw_order = "rgb" if self.raw_order=="bgr" else "bgr"
                    if ev.key == pygame.K_h: self.block_h = 16 if self.block_h==32 else 32
                    if ev.key == pygame.K_o: self.interleave = "row" if self.interleave=="panel" else "panel"
                    if ev.key == pygame.K_l: self.apply_lut = not self.apply_lut

            win.fill((30,30,30))

            with self._lock:
                img = self.last_canvas
            surf = self._make_led_surface(img)
            canvas_w, canvas_h = surf.get_size()

            fps = clock.get_fps()
            active = sum(1 for m in self.modules if m.cfg_seen>0)
            lines = [
                f"bind_ip: {self.bind_ip}  port:{UDP_PORT} (broadcast)",
                f"panels: {self.grid_x}x{self.grid_y}  panel: {self.panel_w}x{self.panel_h}",
                f"canvas(sent): {self.total_w}x{self.total_h}   display(grid): {self.grid_w}x{self.grid_h}   fps: {fps:5.1f}",
                f"assemblies: {len(self.assemblies)}  active panels (cfg): {active}",
                f"last frame: id={self.last_frame_id}  type={self.last_frame_type}  bytes={self.last_frame_bytes}",
                f"raw_order={self.raw_order}  block_h={self.block_h}  interleave={self.interleave}  LUT={'on' if self.apply_lut else 'off'}",
                f"panel_gap={self.panel_gap}px  led_gap={self.led_gap}px",
                "keys: [I]ids  [S]stats  [G]grid",
                "[R]raw-order  [H]block-h  [O]order  [L]lut",
            ]
            text_h = (len(lines) if self.show_stats else 0) * (font.get_height()+4) + 8
            desired_w = max(canvas_w + self.outer_margin*2, 320)
            desired_h = canvas_h + self.outer_margin*2 + text_h
            cur_w, cur_h = win.get_size()
            if desired_w != cur_w or desired_h != cur_h:
                win = pygame.display.set_mode((desired_w, desired_h))

            start_x = (desired_w - canvas_w) // 2
            start_y = self.outer_margin
            win.blit(surf, (start_x, start_y))

            # Panel-Gitter (Anzeige-Koordinaten nach place_mode)
            if self.show_grid:
                scale = (self.led_gap + 1)
                for m in self.modules:
                    dx, dy = self._dst_xy(m.mac16, m.offx, m.offy)
                    px = start_x + dx * scale
                    py = start_y + dy * scale
                    w  = m.width  * scale - self.led_gap
                    h  = m.height * scale - self.led_gap
                    col = (140,140,140) if m.cfg_seen>0 else (60,60,60)
                    pygame.draw.rect(win, col, pygame.Rect(px, py, w, h), 1)
                    if self.panel_gap > 0:
                        pygame.draw.line(win, (40,40,40), (px+w, py), (px+w, py+h))
                        pygame.draw.line(win, (40,40,40), (px, py+h), (px+w, py+h))

            # Zieladressierte Marker rendern
            now = time.time()
            if self._marks:
                scale = (self.led_gap + 1)
                stale = []
                for mac16, (col, until_ts) in self._marks.items():
                    if now > until_ts:
                        stale.append(mac16); continue
                    m = self.panel_by_mac16.get(mac16)
                    if not m:
                        continue
                    dx, dy = self._dst_xy(mac16, m.offx, m.offy)
                    px = start_x + dx * scale
                    py = start_y + dy * scale
                    w  = m.width  * scale - self.led_gap
                    h  = m.height * scale - self.led_gap
                    pygame.draw.rect(win, col, pygame.Rect(px, py, w, h), 3)
                    pygame.draw.circle(win, col, (px+8, py+8), 6)
                for k in stale:
                    self._marks.pop(k, None)

            # IDs
            if self.show_ids:
                scale = (self.led_gap + 1)
                for m in self.modules:
                    dx, dy = self._dst_xy(m.mac16, m.offx, m.offy)
                    px = start_x + dx * scale + 6
                    py = start_y + dy * scale + 4
                    label = f"{m.mac32:08X}  {m.hw}"
                    shadow = tiny.render(label, True, (0,0,0))
                    txt    = tiny.render(label, True, (120,180,255))
                    win.blit(shadow, (px+1, py+1))
                    win.blit(txt, (px, py))
                    px = start_x + dx * scale + 6
                    py = start_y + dy * scale + 14
                    label = f"mac16={m.mac16} nb={m.numBlock}"
                    shadow = tiny.render(label, True, (0,0,0))
                    txt    = tiny.render(label, True, (120,180,255))
                    win.blit(shadow, (px+1, py+1))
                    win.blit(txt, (px, py))

            if self.show_stats:
                base_y = start_y + canvas_h + 8
                for i, t in enumerate(lines):
                    win.blit(font.render(t, True, (230,230,230)), (8, base_y + i*(font.get_height()+4)))

            pygame.display.flip()
            clock.tick(60)
        pygame.quit()

    def run_udp(self):
        log.info("Listening on UDP %d (bind_ip=%s) ...", UDP_PORT, self.bind_ip)
        self.sock.settimeout(0.2)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(9000)
            except socket.timeout:
                continue
            except Exception as e:
                log.error("recv error: %s", e); continue
            if len(data) < 3 or data[0]!=HDR0 or data[1]!=HDR1:
                continue

            cmd = data[2]
            if cmd == MSG_CONFIG:
                self.handle_config(data)
            elif cmd == MSG_FRAME:
                self.handle_frame_packet(data)
            elif cmd == MSG_FRAME_FINISH:
                self.handle_frame_finish(data)
            elif cmd == MSG_SYNC:
                self.handle_sync(data, addr)
            elif cmd == MSG_GAMMA:
                self._handle_gamma(data)
            elif cmd == MSG_A0:
                self._handle_a0(data)
            elif cmd == MSG_STATE:
                # Strict: Display an/aus respektieren
                if self.strict and len(data) >= 4:
                    self.display_enabled = bool(data[3])
                # keine weitere Aktion
            elif cmd == MSG_REG_REQ:
                self._send_register_broadcast()
            elif cmd in (MSG_REGISTER,):
                pass
            else:
                self._dump(f"nuvo_unknown_{cmd}_{int(time.time())}.bin", data)

    def stop(self):
        self.running = False
        try: self.sock.close()
        except: pass

# ---------- CLI ----------
def parse_grid(s:str) -> Tuple[int,int]:
    a,b = s.lower().split("x")
    return int(a), int(b)

def parse_size(s:str) -> Tuple[int,int]:
    a,b = s.lower().split("x")
    return int(a), int(b)

def parse_rgb(s:str) -> Tuple[int,int,int]:
    s = s.strip()
    if s.startswith("#"):
        v = s[1:]
        if len(v) == 6:
            return int(v[0:2],16), int(v[2:4],16), int(v[4:6],16)
        raise argparse.ArgumentTypeError("RGB Hex erwartet wie #1e1e1e")
    parts = s.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("RGB erwartet wie 30,30,30")
    return tuple(max(0, min(255, int(x))) for x in parts)  # type: ignore

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bind-ip", default="0.0.0.0", help="z.B. 0.0.0.0 oder 127.0.0.2")
    p.add_argument("--panels", default="1x1", type=parse_grid)
    p.add_argument("--panel",  default="128x128", type=parse_size)
    p.add_argument("--raw-order", default="bgr", choices=("bgr","rgb"))
    p.add_argument("--dump", action="store_true", help="schreibe Debug-Dumps (Frames/Unknown/Gamma)")
    p.add_argument("--dump-dir", default=None, help="Pfad für Dumps (default: Temp)")
    p.add_argument("--respect-config-offsets", action="store_true",
                   help="nutze Offsets aus CONFIG statt fixe Grid-Positionen (default: aus)")
    p.add_argument("--no-apply-lut", dest="apply_lut", action="store_false",
                   help="Gamma-LUT nicht anwenden (Default: an)")
    p.add_argument("--block-h", type=int, default=32, choices=(16,32), help="RAW Blockhöhe (default 32)")
    p.add_argument("--interleave", default="panel", choices=("panel","row"), help="Deinterleave-Order (default panel)")
    p.add_argument("--panel-gap", type=int, default=1, help="sichtbarer Spalt/Umriss zwischen Panels (Default 1)")
    p.add_argument("--led-gap", type=int, default=1, help="Pixel-Gap rechts/unten für LED-Look (Default 1, 0=aus)")
    p.add_argument("--led-gap-color", type=parse_rgb, default=(26,26,26), help="Farbe für Pixel-Gap, z.B. '#1e1e1e' oder '30,30,30'")
    p.add_argument("--outer-margin", type=int, default=12, help="Außenrand um das Canvas (Default 12)")
    p.add_argument("--order-mode", choices=("mac16","nblock","config","grid"),
                   default="config", help="Panel-Reihenfolge/Adressierung (Default mac16)")
    p.add_argument("--place-mode", choices=("layout","id"),
                   default="layout", help="Platzierung: 'layout'=CONFIG-Offsets, 'id'=lokal nach IDs")
    p.add_argument("--strict", action="store_true", default=False,
               help="Strenger Protokollmodus (Paketgrößen/Indizes/Typen strikt prüfen)")
    p.add_argument("--mac32-base", type=lambda s: int(s,0), default=0x10B00000,
                    help="high 16 bits for mac32; low 16 bits werden mit mac16 überschrieben")
    p.add_argument("--mac16-start", type=lambda s: int(s,0), default=0x0001,
                    help="erster mac16-Wert für Panel #1 (z.B. 0x0001)")
    args = p.parse_args()

    sim = NuvoSimulator(
        args.bind_ip, args.panels, args.panel,
        raw_order=args.raw_order,
        do_dumps=args.dump,
        dump_dir=args.dump_dir,
        respect_config_offsets=args.respect_config_offsets,
        apply_lut=getattr(args, "apply_lut", True),
        block_h=args.block_h,
        interleave=args.interleave,
        panel_gap=args.panel_gap,
        led_gap=args.led_gap,
        led_gap_color=args.led_gap_color,
        outer_margin=args.outer_margin,
        mac32_base=args.mac32_base & 0xFFFF0000,
        mac16_start=args.mac16_start & 0xFFFF,
    )
    sim.order_mode = args.order_mode
    sim.place_mode = args.place_mode

    sim.strict = args.strict
    sim._apply_strict_defaults()
    log.info(f"Setting strict mode: {sim.strict}")

    t = threading.Thread(target=sim.run_udp, daemon=True)
    t.start()
    try:
        sim.start_gui()
    finally:
        sim.stop()
        t.join(timeout=1.0)

if __name__ == "__main__":
    main()
