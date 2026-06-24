# SPDX-License-Identifier: WTFPL
"""Decode a VIA Labs VL8xx flash image: container, USB descriptors, 8051 entry.

Three layers, mirroring the DP-side decode's proven/inferred discipline:
  * container  -- the flash header + bank/section map (magic PROVEN; field
                  meanings beyond the code-start pointer are INFERRED).
  * descriptors-- the embedded USB descriptors (PROVEN: USB 2.0/3.x spec).
  * code       -- 8051 reset/interrupt vectors + entry, via vlidump.i8051
                  (opcode decode PROVEN; routine semantics not decoded).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import i8051

_FAMILY = {b"\x05\x18": "VL822", b"\x05\x38": "VL817"}
_DESC = {1: "DEVICE", 2: "CONFIG", 3: "STRING", 4: "INTERFACE", 5: "ENDPOINT",
         6: "DEV_QUALIFIER", 7: "OTHER_SPEED_CFG", 0x0B: "IAD", 0x0F: "BOS",
         0x10: "DEV_CAP", 0x21: "HID", 0x30: "SS_EP_COMPANION"}


@dataclass
class Section:
    start: int
    end: int
    kind: str


@dataclass
class Container:
    family: str
    magic: bytes
    code_start: int
    raw_type: int
    raw_word6: int
    raw_tail: bytes
    sections: list[Section] = field(default_factory=list)


def decode_container(blob: bytes) -> Container:
    magic = blob[0:2]
    fam = _FAMILY.get(magic, "unknown")
    code_start = (blob[4] | (blob[5] << 8)) * 0x100   # page count * 256
    c = Container(fam, magic, code_start, blob[2] | (blob[3] << 8),
                  blob[6] | (blob[7] << 8), blob[0x1e:0x20])
    # section map at 256B granularity
    BL = 256

    def kind(b):
        if all(x == 0xFF for x in b):
            return "erased"
        if all(x == 0x00 for x in b):
            return "zero"
        return "data"
    cur = None
    start = 0
    for off in range(0, len(blob), BL):
        k = kind(blob[off:off + BL])
        if k != cur:
            if cur is not None:
                c.sections.append(Section(start, off, cur))
            cur, start = k, off
    c.sections.append(Section(start, len(blob), cur))
    return c


@dataclass
class Descriptor:
    offset: int
    length: int
    dtype: int
    fields: dict
    text: str


def _device_desc(b: bytes, o: int) -> Descriptor:
    f = {
        "bcdUSB": f"{b[o+3]:x}.{b[o+2]:02x}",
        "bDeviceClass": b[o + 4], "bMaxPacketSize0": b[o + 7],
        "idVendor": b[o + 8] | (b[o + 9] << 8),
        "idProduct": b[o + 10] | (b[o + 11] << 8),
        "bcdDevice": f"{b[o+13]:x}.{b[o+12]:02x}",
        "iManufacturer": b[o + 14], "iProduct": b[o + 15],
        "iSerial": b[o + 16], "bNumConfigurations": b[o + 17],
    }
    t = (f"DEVICE  USB {f['bcdUSB']}  class 0x{f['bDeviceClass']:02x}  "
         f"{f['idVendor']:04x}:{f['idProduct']:04x}  bcdDevice {f['bcdDevice']}  "
         f"MaxPkt0 {f['bMaxPacketSize0']}  nCfg {f['bNumConfigurations']}")
    return Descriptor(o, b[o], 1, f, t)


def find_descriptors(blob: bytes) -> list[Descriptor]:
    """Find device descriptors and the standalone string descriptors."""
    out: list[Descriptor] = []
    n = len(blob)
    for o in range(n - 18):
        # device descriptor: bLength=0x12, bType=0x01, plausible bMaxPacketSize0
        if blob[o] == 0x12 and blob[o + 1] == 0x01 and blob[o + 7] in (8, 9, 16, 32, 64) \
                and (blob[o + 8] | (blob[o + 9] << 8)) == 0x2109:
            out.append(_device_desc(blob, o))
    return out


def find_strings(blob: bytes):
    """UTF-16LE USB string descriptors: [len, 0x03, utf16...]."""
    res = []
    i = 0
    n = len(blob)
    while i < n - 2:
        ln = blob[i]
        if blob[i + 1] == 0x03 and 4 <= ln <= 64 and i + ln <= n and ln % 2 == 0:
            body = blob[i + 2:i + ln]
            if body and all(32 <= body[j] < 127 and body[j + 1] == 0
                            for j in range(0, len(body), 2)):
                res.append((i, body.decode("utf-16-le")))
                i += ln
                continue
        i += 1
    return res


def disasm_entry(blob: bytes, container: Container, count: int = 24):
    """Disassemble the reset vector table + first instructions of the entry."""
    code = blob[container.code_start:]
    return list(i8051.disasm(code, base=0x0000, count=count))


VEC = {0x00: "RESET", 0x03: "INT0", 0x0B: "TIMER0", 0x13: "INT1",
       0x1B: "TIMER1", 0x23: "UART", 0x2B: "TIMER2"}


def render(blob: bytes) -> str:
    c = decode_container(blob)
    L = [f"=== VL8xx firmware image ({len(blob)} bytes) ==="]
    L.append(f"container : {c.family}  magic {c.magic.hex(' ')}  "
             f"code@0x{c.code_start:04x}  type 0x{c.raw_type:04x}  "
             f"word6 0x{c.raw_word6:04x}  tail {c.raw_tail.hex(' ')}")
    L.append("")
    L.append("section map (256B):")
    for s in c.sections:
        if s.kind == "data":
            L.append(f"  0x{s.start:05x}-0x{s.end:05x}  {s.end - s.start:6d}B  {s.kind}")

    L.append("")
    L.append("8051 vectors + entry (code 0x0000 = flash 0x%04x):" % c.code_start)
    for ins in disasm_entry(blob, c, count=10):
        tag = f"  ; {VEC[ins.addr]}" if ins.addr in VEC else ""
        tgt = f" -> 0x{ins.target:04X}" if ins.target is not None else ""
        L.append(f"  {ins.addr:04X}: {ins.raw.hex(' '):<10} {ins.text}{tgt}{tag}")

    code = blob[c.code_start:]
    reset = i8051.decode_one(code, 0, 0).target
    if reset is not None and reset < len(code):
        L.append("")
        L.append(f"reset handler @0x{reset:04X}:")
        for ins in i8051.disasm(code, base=0, start=reset, count=14):
            tgt = f" -> 0x{ins.target:04X}" if ins.target is not None else ""
            L.append(f"  {ins.addr:04X}: {ins.raw.hex(' '):<10} {ins.text}{tgt}")

    L.append("")
    descs = find_descriptors(blob)
    L.append(f"USB device descriptors found: {len(descs)}")
    for d in descs:
        L.append(f"  @0x{d.offset:05x}  {d.text}")

    L.append("")
    strs = find_strings(blob)
    L.append(f"USB string descriptors: {len(strs)}")
    for off, s in strs:
        if s.strip():
            L.append(f"  @0x{off:05x}  {s!r}")
    return "\n".join(L)
