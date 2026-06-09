# SPDX-License-Identifier: WTFPL
"""Read the VMM53xx register and SRAM space over an enabled RC session."""
from __future__ import annotations

import struct
from typing import Callable, Iterable

from . import addresses as _addr


def dump_registers(rc, addrs: Iterable[int],
                   progress: Callable[[int, int], None] | None = None
                   ) -> dict[int, int]:
    """Read every 32-bit register in ``addrs``.

    Consecutive addresses are coalesced into contiguous runs and fetched with a
    single RC memory read each (the RC layer chunks to the AUX limit internally),
    then sliced back into per-address u32 values.
    """
    addrs = sorted(set(addrs))
    runs = _addr.coalesce(addrs)
    total = len(addrs)
    out: dict[int, int] = {}
    done = 0
    for start, count in runs:
        raw = rc.read_memory(start, count * 4)
        for i in range(count):
            out[start + i * 4] = struct.unpack_from("<I", raw, i * 4)[0]
        done += count
        if progress:
            progress(done, total)
    return out


def dump_memory(rc, start: int, length: int,
                progress: Callable[[int, int], None] | None = None) -> bytes:
    """Read a contiguous block of chip memory (e.g. the low SRAM/EDID region)."""
    out = bytearray()
    pos = start
    step = 256
    while len(out) < length:
        n = min(step, length - len(out))
        out += rc.read_memory(pos, n)
        pos += n
        if progress:
            progress(len(out), length)
    return bytes(out)
