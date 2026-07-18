# SPDX-License-Identifier: WTFPL
"""vmmdump command-line interface.

    sudo python3 -m vmmdump                 # detect + identity + decoded summary
    sudo python3 -m vmmdump --raw out.txt   # + full register dump to a file
    sudo python3 -m vmmdump --edid          # + recover monitor EDIDs
    sudo python3 -m vmmdump --slots         # MST trunk + VC payload slot table
    python3 -m vmmdump --decode-file dump.txt   # offline: decode an existing dump
"""
from __future__ import annotations

import argparse
import os
import sys


def _eprint(*a, **kw):
    kw.setdefault("file", sys.stderr)
    print(*a, **kw)


def cmd_list_devices(args) -> int:
    from . import detect
    cands = detect.discover(args.transport, args.gpu)
    if not cands:
        _eprint("no candidates found")
        return 1
    for c in cands:
        if c.transport is None:
            print(f"  - {c.label}")
            continue
        mark = "  <== Synaptics MST hub" if c.is_hub else ""
        print(f"  [{c.score()}] {c.label}  OUI={c.oui.hex(':')} "
              f"chip=0x{c.chip_id:04X} RC_CAP=0x{c.rc_cap:02X}{mark}")
    return 0


def _close_candidate(cand) -> None:
    if cand._owner is not None and hasattr(cand._owner, "close"):
        cand._owner.close()
    elif hasattr(cand.transport, "close"):
        cand.transport.close()


def _find_hub(args):
    from . import detect
    try:
        return detect.find_hub(args.transport, args.gpu)
    except RuntimeError as e:
        _eprint(f"error: {e}")
        if os.geteuid() != 0:
            _eprint("hint: AUX/RM access needs root -- try sudo")
        return None


def cmd_slots(args) -> int:
    """MST trunk + VC payload slot table. Native DPCD only -- no RC session."""
    from .slots import read_slots, render_slots
    cand = _find_hub(args)
    if cand is None:
        return 2
    _eprint(f"using {cand.label}  chip=0x{cand.chip_id:04X}")
    try:
        text = render_slots(read_slots(cand.transport))
    finally:
        _close_candidate(cand)
    print(text)
    if args.slots is not True:
        with open(args.slots, "w") as f:
            f.write(text + "\n")
        _eprint(f"wrote slots -> {args.slots}")
    return 0


def cmd_offline(args) -> int:
    """Decode an existing dump.txt with no hardware."""
    from . import addresses, decode, report
    regs = addresses.parse_registers(args.decode_file)
    if not regs:
        _eprint(f"no registers parsed from {args.decode_file}")
        return 1
    edids = None
    if args.edid:
        from .edid import scan_edids
        rows = addresses.parse_memory(args.decode_file)
        if rows:
            base = min(rows)
            blob = bytearray()
            for off in range(base, max(rows) + 16, 16):
                blob += rows.get(off, b"\x00" * 16)
            edids = scan_edids(bytes(blob), base)

    # identity from the register/DPCD-ish values we have offline
    class _Id:
        def lines(self):
            cid = regs.get(0x200D38, 0)
            return [f"CHIP ID         : VMM{(cid >> 16) & 0xFFFF:04X} (from 0x200D38)",
                    "(offline decode; identity DPCD not present in dump)"]
    decoded = decode.decode_all(regs)
    text = report.render_text(_Id(), decoded, edids)
    print(text)
    if args.decode and args.decode is not True:
        report.write_decoded(args.decode, _Id(), decoded, edids)
        _eprint(f"wrote {args.decode}")
    return 0


def cmd_live(args) -> int:
    from . import decode, identity as ident_mod, report
    from .rc import SynapticsRC

    cand = _find_hub(args)
    if cand is None:
        return 2
    _eprint(f"using {cand.label}  chip=0x{cand.chip_id:04X}")
    transport = cand.transport

    rc = SynapticsRC(transport)
    regs = None
    edids = None
    try:
        ident = ident_mod.read_identity(transport)
        rc.enable()
        ident = ident_mod.read_identity(transport, rc)  # adds board id
        if args.raw:
            from . import addresses, dumper
            if args.addresses_from:
                addrs = addresses.register_addresses(args.addresses_from)
            else:
                _eprint("note: no --addresses-from; dumping decoder register set only")
                addrs = sorted(decode.read_decode_regs(rc))

            def _p(d, t):
                _eprint(f"\r  reading {d}/{t} regs", end="")
            regs = dumper.dump_registers(rc, addrs, _p)
            _eprint()
        else:
            regs = decode.read_decode_regs(rc)
        if args.edid:
            from .edid import read_edids_via_rc
            edids = read_edids_via_rc(rc)
    finally:
        try:
            rc.disable()
        except Exception:
            pass
        _close_candidate(cand)

    decoded = decode.decode_all(regs)
    print(report.render_text(ident, decoded, edids))

    if args.raw:
        report.write_raw(args.raw, ident, regs, edids=edids)
        _eprint(f"wrote raw dump -> {args.raw}")
    if args.decode and args.decode is not True:
        report.write_decoded(args.decode, ident, decoded, edids)
        _eprint(f"wrote decoded -> {args.decode}")
    if args.json:
        report.write_json(args.json, ident, decoded, edids, regs)
        _eprint(f"wrote json -> {args.json}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vmmdump",
                                description="Extract VMM5310 DP-MST hub info over DP AUX")
    p.add_argument("--transport", choices=["auto", "nvrm", "drm"], default="auto")
    p.add_argument("--gpu", type=int, default=0, help="NVIDIA GPU index (nvrm)")
    p.add_argument("--list-devices", action="store_true",
                   help="enumerate AUX sinks and exit")
    p.add_argument("--slots", metavar="PATH", nargs="?", const=True,
                   help="read MST trunk + VC payload slot table (native DPCD "
                        "only, no RC session) and exit; optionally write to PATH")
    p.add_argument("--raw", metavar="PATH", nargs="?", const="vmm_dump.out.txt",
                   help="write full register dump (needs --addresses-from for the "
                        "complete VMMTool address list)")
    p.add_argument("--addresses-from", metavar="DUMP",
                   help="take the register address list from an existing dump.txt")
    p.add_argument("--decode", metavar="PATH", nargs="?", const=True,
                   help="write decoded report to PATH")
    p.add_argument("--json", metavar="PATH", help="write structured JSON to PATH")
    p.add_argument("--edid", action="store_true", help="recover EDIDs from hub SRAM")
    p.add_argument("--decode-file", metavar="DUMP",
                   help="offline: decode an existing dump.txt, no hardware")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    if args.list_devices:
        return cmd_list_devices(args)
    if args.slots:
        return cmd_slots(args)
    if args.decode_file:
        return cmd_offline(args)
    return cmd_live(args)


if __name__ == "__main__":
    sys.exit(main())
