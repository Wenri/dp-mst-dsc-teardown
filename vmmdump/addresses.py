# SPDX-License-Identifier: WTFPL
"""Parse a VMMTool ``dump.txt`` and describe the chip's address layout.

``dump.txt`` is the Windows ground-truth capture. It has three data sections:

  * ``Register data dump...``  -- 32-bit registers, two line styles VMMTool mixes:
        ``200000h: 0x85F2439B (-2047720549)``         one value
        ``380600h: 00000000 00000000 00000000 ...``   4 values at +0,+4,+8,+C
  * ``Memory data dump...``    -- 16 bytes/line of low SRAM from 0x00000000
  * ``<name> EDID:``           -- three monitor EDIDs as hex byte rows

We use it two ways: derive the exact register-address list to read back from the
hardware, and build an address->value map to diff a live dump against.
"""
from __future__ import annotations

import re
import struct

_RE_SINGLE = re.compile(r"^([0-9A-Fa-f]+)h:\s*0x([0-9A-Fa-f]+)\b")
_RE_MULTI = re.compile(r"^([0-9A-Fa-f]+)h:\s*((?:[0-9A-Fa-f]{8}(?:\s+|$))+)$")
_RE_MEMROW = re.compile(r"^([0-9A-Fa-f]+)h:\s*((?:[0-9A-Fa-f]{2}\s*)+)$")

SEC_REG = "Register data dump..."
SEC_REG_END = "Register data dump finished"
SEC_MEM = "Memory data dump..."
SEC_MEM_END = "Memory data dump finished"


def parse_registers(path: str) -> dict[int, int]:
    """Return ``{address: u32}`` for every register in the register section."""
    regs: dict[int, int] = {}
    in_sec = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s == SEC_REG:
                in_sec = True
                continue
            if s == SEC_REG_END:
                break
            if not in_sec or not s:
                continue
            m = _RE_SINGLE.match(s)
            if m:
                regs[int(m.group(1), 16)] = int(m.group(2), 16) & 0xFFFFFFFF
                continue
            m = _RE_MULTI.match(s)
            if m:
                base = int(m.group(1), 16)
                for i, word in enumerate(m.group(2).split()):
                    regs[base + i * 4] = int(word, 16) & 0xFFFFFFFF
    return regs


def parse_memory(path: str) -> dict[int, bytes]:
    """Return ``{base_address: 16_bytes}`` rows for the memory-dump section."""
    rows: dict[int, bytes] = {}
    in_sec = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s == SEC_MEM:
                in_sec = True
                continue
            if s == SEC_MEM_END:
                break
            if not in_sec or not s:
                continue
            m = _RE_MEMROW.match(s)
            if m:
                rows[int(m.group(1), 16)] = bytes(
                    int(b, 16) for b in m.group(2).split())
    return rows


def register_addresses(path: str) -> list[int]:
    return sorted(parse_registers(path))


def memory_span(path: str) -> tuple[int, int]:
    """Return ``(start, length)`` covering the memory-dump section."""
    rows = parse_memory(path)
    if not rows:
        return (0, 0)
    start = min(rows)
    end = max(rows) + len(rows[max(rows)])
    return (start, end - start)


def coalesce(addresses: list[int], stride: int = 4) -> list[tuple[int, int]]:
    """Group sorted addresses into ``(start, count)`` runs of constant stride."""
    runs: list[tuple[int, int]] = []
    it = iter(sorted(set(addresses)))
    try:
        start = prev = next(it)
    except StopIteration:
        return runs
    count = 1
    for a in it:
        if a == prev + stride:
            count += 1
        else:
            runs.append((start, count))
            start = a
            count = 1
        prev = a
    runs.append((start, count))
    return runs


def assemble_u32(raw: bytes) -> int:
    """4 little-endian bytes -> the u32 as printed by VMMTool (0xAABBCCDD)."""
    return struct.unpack("<I", raw)[0]


def to_signed32(value: int) -> int:
    return value - 0x100000000 if value & 0x80000000 else value
