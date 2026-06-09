# SPDX-License-Identifier: WTFPL
"""Render extraction results: raw register dump, decoded text, JSON."""
from __future__ import annotations

import json as _json
from dataclasses import asdict

from . import decode as _decode
from .addresses import to_signed32


def write_raw(path: str, identity, regs: dict[int, int],
              memory: bytes | None = None, edids=None) -> None:
    """A complete, re-parseable register dump (parsed back by addresses.py)."""
    lines = ["VMM register dump (vmmdump)", ""]
    lines += identity.lines()
    lines += ["", "Register data dump..."]
    for addr in sorted(regs):
        v = regs[addr]
        lines.append(f"{addr:06X}h: 0x{v:08X} ({to_signed32(v)})")
    lines.append("Register data dump finished")
    if memory:
        lines += ["", "Memory data dump..."]
        for off in range(0, len(memory), 16):
            row = memory[off:off + 16]
            hexs = " ".join(f"{b:02X}" for b in row)
            lines.append(f"{off:08X}h: {hexs}")
        lines.append("Memory data dump finished")
    if edids:
        for i, e in enumerate(edids):
            lines += ["", f"EDID {i} ({e.manufacturer} {e.product_code:04X}):"]
            for off in range(0, len(e.raw), 16):
                lines.append(" ".join(f"{b:02X}" for b in e.raw[off:off + 16]))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def render_text(identity, decoded, edids=None) -> str:
    out = ["=== VMM5310 identity ==="]
    out += identity.lines()
    out += ["", "=== link / streams / DSC / outputs ==="]
    out += _decode.render(decoded)
    if edids:
        out += ["", "=== EDIDs (recovered from hub SRAM) ==="]
        for e in edids:
            out.append("  " + e.line())
    return "\n".join(out)


def write_decoded(path: str, identity, decoded, edids=None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_text(identity, decoded, edids) + "\n")


def build_json(identity, decoded, edids=None, regs=None) -> dict:
    d = {
        "identity": asdict(identity) | {"oui": identity.oui.hex(),
                                        "branch_devid": identity.branch_devid.hex()},
        "rx_link": asdict(decoded.rx_link),
        "mst_slots": decoded.mst_slots,
        "fw_build_stamp": decoded.fw_build_stamp,
        "streams": [asdict(s) for s in decoded.streams],
        "tx": [asdict(t) for t in decoded.tx],
    }
    if edids is not None:
        d["edids"] = [{k: v for k, v in asdict(e).items() if k != "raw"}
                      | {"raw": e.raw.hex()} for e in edids]
    if regs is not None:
        d["registers"] = {f"0x{a:06X}": regs[a] for a in sorted(regs)}
    return d


def write_json(path: str, identity, decoded, edids=None, regs=None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump(build_json(identity, decoded, edids, regs), fh, indent=2)
