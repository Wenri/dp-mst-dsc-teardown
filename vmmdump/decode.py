# SPDX-License-Identifier: WTFPL
"""Decode VMM5310 registers into the human-readable view VMMTool prints.

Every field here is derived from registers verified byte-identical to the
Windows ``dump.txt`` ground truth. Standards-based fields (DP link rate, lane
status, VESA DSC 1.2 PPS, MSA timings) decode authoritatively; chip-proprietary
state words are reported but labelled as raw where their bit map is unknown.

Register meanings come from ``VMM5310_dump_decoded.md`` (corrected: each RFRM's
Main Stream Attributes live at base+0x30, packed (vertical<<16)|horizontal).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# link rate code (reg 0x2107C0, value/100 = Gbps/lane) -> name
_LINK_RATE = {162: "RBR (1.62)", 270: "HBR (2.70)", 540: "HBR2 (5.40)",
              810: "HBR3 (8.10)"}

RFRM_BASES = {0: 0x220800, 1: 0x220C00, 2: 0x221000}
RFRM_DSC = {0: 0x220A00, 1: 0x220E00, 2: 0x221200}
TX_BASES = {0: 0x300000, 1: 0x340000, 2: 0x380000}
TX_LINK = {0: 0x310000, 1: 0x350000, 2: 0x390000}


def _hi(v):
    return (v >> 16) & 0xFFFF


def _lo(v):
    return v & 0xFFFF


@dataclass
class Timing:
    ht: int = 0
    vt: int = 0
    ha: int = 0
    va: int = 0
    hs: int = 0
    vs: int = 0
    hsw: int = 0
    vsw: int = 0
    hpol: int = 0
    vpol: int = 0

    @property
    def active(self) -> bool:
        return self.ha > 0 and self.va > 0

    def line(self) -> str:
        return (f"HT:{self.ht} VT:{self.vt} HA:{self.ha} VA:{self.va} "
                f"HS:{self.hs} VS:{self.vs} HSW:{self.hsw} VSW:{self.vsw} "
                f"HPOL:{self.hpol} VPOL:{self.vpol}")


def read_timing(regs, base: int) -> Timing:
    g = regs.get
    t = Timing()
    # bit15 of each sync half-word carries polarity; mask it off the width
    v = g(base + 0x30, 0); t.vt, t.ht = _hi(v), _lo(v)
    v = g(base + 0x34, 0); t.vs, t.hs = _hi(v) & 0x7FFF, _lo(v) & 0x7FFF
    v = g(base + 0x38, 0); t.va, t.ha = _hi(v), _lo(v)
    v = g(base + 0x3C, 0)
    t.vsw, t.hsw = _hi(v) & 0x7FFF, _lo(v) & 0x7FFF
    t.vpol, t.hpol = (_hi(v) >> 15) & 1, (_lo(v) >> 15) & 1
    return t


@dataclass
class DscInfo:
    enabled: bool
    ctrl: int
    version: str = ""
    line_buf_depth: int = 0
    bpc: int = 0
    bpp: float = 0.0
    pic_w: int = 0
    pic_h: int = 0
    slice_w: int = 0
    slice_h: int = 0
    chunk_size: int = 0
    ratio: float = 0.0


def read_dsc(regs, rfrm: int) -> DscInfo:
    base = RFRM_DSC[rfrm]
    ctrl = regs.get(base, 0)
    enabled = bool(ctrl & 0x02000000)  # 0xA2.. = on, 0x40../0x80.. = off/idle
    info = DscInfo(enabled=enabled, ctrl=ctrl)
    if not enabled:
        return info
    ver = regs.get(base + 0x08, 0)
    info.version = f"{(ver >> 28) & 0xF}.{(ver >> 24) & 0xF}"  # 0x12.. -> 1.2
    info.bpc = (ver >> 4) & 0xF        # PPS byte 3 [7:4] bits_per_component
    info.line_buf_depth = ver & 0xF
    bpp_h = regs.get(base + 0x0C, 0)
    info.bpp = (bpp_h >> 16 & 0xFFF) / 16.0       # 0x0A0 -> 10.00
    info.slice_h = _lo(bpp_h)
    pic = regs.get(base + 0x10, 0)
    info.pic_w, info.pic_h = _hi(pic), _lo(pic)
    sl = regs.get(base + 0x14, 0)
    info.slice_w, info.chunk_size = _hi(sl), _lo(sl)
    if info.bpp:
        src = 3 * info.bpc if info.bpc else 24      # RGB, bpc from PPS
        info.ratio = src / info.bpp
    return info


@dataclass
class RxLink:
    lanes_trained: int
    lane_status: list[int]
    link_rate_code: int
    link_rate_name: str

    def line(self) -> str:
        return (f"RX trunk: {self.lanes_trained} lane(s), "
                f"link rate {self.link_rate_name} Gb/s/lane")


def read_rx_link(regs) -> RxLink:
    ls = regs.get(0x210000, 0)
    lanes = [(ls >> (4 * i)) & 0xF for i in range(4)]
    trained = sum(1 for n in lanes if n == 0x7)  # CR_DONE|EQ_DONE|SYMBOL_LOCKED
    code = regs.get(0x2107C0, 0)
    return RxLink(trained, lanes, code, _LINK_RATE.get(code, f"{code/100:.2f}"))


@dataclass
class Stream:
    index: int
    active: bool
    ctrl: int
    timing: Timing
    dsc: DscInfo


def read_streams(regs) -> list[Stream]:
    out = []
    for i in (0, 1, 2):
        base = RFRM_BASES[i]
        ctrl = regs.get(base, 0)
        t = read_timing(regs, base)
        dsc = read_dsc(regs, i)
        out.append(Stream(i, t.active, ctrl, t, dsc))
    return out


@dataclass
class TxOut:
    index: int
    kind: str          # "HDMI" | "DP" | "idle"
    lanes: int
    timing: Timing


def read_tx(regs) -> list[TxOut]:
    out = []
    for i in (0, 1, 2):
        link = TX_LINK[i]
        link0 = regs.get(link, 0)
        chanmap = regs.get(link + 0x50, 0)
        if link0 in (0, 0xFEEDB001) or chanmap in (0, 0xFEEDB001):
            out.append(TxOut(i, "idle", 0, Timing()))
            continue
        low = chanmap & 0xFF
        if low == 0x00:
            kind, lanes = "HDMI", 0
        else:
            kind, lanes = "DP", bin(low).count("1")
        # TX timing lives at link+0xC8 (VT/HT) and +0xD0 (VA/HA); HDMI omits it
        t = Timing()
        v = regs.get(link + 0xC8, 0)
        if v not in (0, 0xFEEDB001):
            t.vt, t.ht = _hi(v), _lo(v)
            v = regs.get(link + 0xD0, 0)
            t.va, t.ha = _hi(v), _lo(v)
        out.append(TxOut(i, kind, lanes, t))
    return out


@dataclass
class Decoded:
    mst_slots: int
    fw_build_stamp: int
    rx_link: RxLink
    streams: list[Stream]
    tx: list[TxOut] = field(default_factory=list)


# spans the decoder needs, for a fast summary without the full register list
DECODE_SPANS = [
    (0x200040, 4), (0x20004C, 4), (0x200D30, 4),
    (0x210000, 4), (0x2107C0, 4),
    (0x220800, 0x44), (0x220A00, 0x18), (0x220C00, 0x44),
    (0x220E00, 4), (0x221000, 0x44), (0x221200, 4),
    (0x300000, 4), (0x310000, 4), (0x310050, 4), (0x3100C8, 0x0C),
    (0x340000, 4),
    (0x380000, 4), (0x390000, 4), (0x390050, 4), (0x3900C8, 0x0C),
]


def read_decode_regs(rc) -> dict:
    """Read just the registers the decoder needs via an enabled RC session."""
    import struct
    regs: dict[int, int] = {}
    for start, length in DECODE_SPANS:
        raw = rc.read_memory(start, length)
        for i in range(0, length, 4):
            regs[start + i] = struct.unpack_from("<I", raw, i)[0]
    return regs


def decode_all(regs) -> Decoded:
    return Decoded(
        mst_slots=regs.get(0x20004C, 0) & 0xFF,
        fw_build_stamp=regs.get(0x200D30, 0),
        rx_link=read_rx_link(regs),
        streams=read_streams(regs),
        tx=read_tx(regs),
    )


def render(d: Decoded) -> list[str]:
    out: list[str] = []
    any_dsc = any(s.dsc.enabled for s in d.streams)
    out.append(f"RX: MST {d.rx_link.lanes_trained}-lane "
               f"{d.rx_link.link_rate_name.split()[0]} mode"
               f"{', DSC ON' if any_dsc else ''}")
    out.append(f"   MST time slots: {d.mst_slots}")
    bs = d.fw_build_stamp
    out.append(f"   FW build stamp: 0x{bs:08X} "
               f"({bs >> 16:04X}-{bs >> 8 & 0xFF:02X}-{bs & 0xFF:02X})")
    for s in d.streams:
        if not s.active and not s.dsc.enabled:
            out.append(f"\nRFRM{s.index}: (idle)")
            continue
        out.append(f"\nRFRM{s.index}: {s.timing.ha}x{s.timing.va}  ctrl=0x{s.ctrl:08X}")
        out.append(f"   {s.timing.line()}")
        if s.dsc.enabled:
            out.append(f"   DSC {s.dsc.version}: PIC {s.dsc.pic_w}x{s.dsc.pic_h}, "
                       f"Slice {s.dsc.slice_w}x{s.dsc.slice_h}, {s.dsc.bpc} bpc -> "
                       f"{s.dsc.bpp:.2f} bpp, ratio {s.dsc.ratio:.1f}:1, "
                       f"chunk {s.dsc.chunk_size}, "
                       f"line-buf {s.dsc.line_buf_depth}")
        else:
            out.append(f"   DSC: off (ctrl=0x{s.dsc.ctrl:08X})")
    for t in d.tx:
        if t.kind == "idle":
            out.append(f"\nTX{t.index}: idle / HPD low")
            continue
        extra = f", {t.lanes} lane(s)" if t.kind == "DP" else ""
        out.append(f"\nTX{t.index}: {t.kind} output{extra}")
        if t.timing.active:
            out.append(f"   {t.timing.ha}x{t.timing.va}  "
                       f"HT:{t.timing.ht} VT:{t.timing.vt}")
    return out
