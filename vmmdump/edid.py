# SPDX-License-Identifier: WTFPL
"""Recover and parse EDIDs the hub holds in its low SRAM.

VMMTool's "Memory data dump" is the chip's low SRAM; the hub keeps the RX
default EDID and the downstream monitor EDIDs there. We read that region over RC
(ReadFromMemory at low addresses) and scan for valid 128-byte EDID blocks.
"""
from __future__ import annotations

from dataclasses import dataclass

EDID_MAGIC = b"\x00\xff\xff\xff\xff\xff\xff\x00"
SRAM_SCAN_LEN = 0x3000  # 12 KiB, matches dump.txt memory section


@dataclass
class Edid:
    offset: int
    raw: bytes
    manufacturer: str
    product_code: int
    serial: int
    week: int
    year: int
    name: str
    pref_w: int
    pref_h: int
    pref_clock_mhz: float

    def line(self) -> str:
        n = self.name or "?"
        tim = (f"{self.pref_w}x{self.pref_h} @ {self.pref_clock_mhz:.2f}MHz"
               if self.pref_w else "n/a")
        return (f"{self.manufacturer} {self.product_code:04X} "
                f'"{n}"  preferred {tim}  (sram@0x{self.offset:04X})')


def _checksum_ok(block: bytes) -> bool:
    return len(block) == 128 and (sum(block) & 0xFF) == 0


def _manufacturer(block: bytes) -> str:
    v = (block[8] << 8) | block[9]
    return "".join(chr(((v >> s) & 0x1F) + 0x40) for s in (10, 5, 0))


def _name(block: bytes) -> str:
    for off in (54, 72, 90, 108):
        d = block[off:off + 18]
        if d[0:2] == b"\x00\x00" and d[3] == 0xFC:
            return d[5:18].split(b"\x0a")[0].decode("ascii", "replace").strip()
    return ""


def _preferred(block: bytes) -> tuple[int, int, float]:
    d = block[54:72]
    clock = (d[0] | (d[1] << 8)) * 10  # kHz
    if clock == 0:
        return (0, 0, 0.0)
    hact = d[2] | ((d[4] >> 4) << 8)
    vact = d[5] | ((d[7] >> 4) << 8)
    return (hact, vact, clock / 1000.0)


def parse_edid(block: bytes, offset: int = 0) -> Edid:
    w, h, clk = _preferred(block)
    return Edid(
        offset=offset, raw=block, manufacturer=_manufacturer(block),
        product_code=block[10] | (block[11] << 8),
        serial=int.from_bytes(block[12:16], "little"),
        week=block[16], year=block[17] + 1990, name=_name(block),
        pref_w=w, pref_h=h, pref_clock_mhz=clk,
    )


def scan_edids(blob: bytes, base: int = 0) -> list[Edid]:
    """Find every valid 128-byte EDID base block in ``blob``."""
    out: list[Edid] = []
    seen: set[bytes] = set()
    i = 0
    while True:
        j = blob.find(EDID_MAGIC, i)
        if j < 0:
            break
        block = blob[j:j + 128]
        if _checksum_ok(block):
            if block not in seen:        # collapse identical default-EDID copies
                seen.add(block)
                out.append(parse_edid(block, base + j))
            i = j + 128
        else:
            i = j + 8
    return out


def read_edids_via_rc(rc, length: int = SRAM_SCAN_LEN) -> list[Edid]:
    """Read the low SRAM over RC and return the EDIDs found in it."""
    from . import dumper
    blob = dumper.dump_memory(rc, 0, length)
    return scan_edids(blob, 0)
