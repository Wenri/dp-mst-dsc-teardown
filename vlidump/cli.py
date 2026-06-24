# SPDX-License-Identifier: WTFPL
"""vlidump -- read the dock's VIA Labs USB hub (the USB half of the teardown).

    sudo python3 -m vlidump                     # identify chip + flash JEDEC id
    sudo python3 -m vlidump --regs f88c,f88e    # read arbitrary registers
    sudo python3 -m vlidump --spi-dump fw.bin --spi-len 0x80000   # dump SPI flash

Companion to ``vmmdump``: that tool reads the MegaChips VMM5310 DP-MST hub over
DP AUX; this one reads the VIA Labs hub that carries USB 3 over the same Type-C
cable's other lane pair. Read-only -- it never writes the hub or its flash.
"""
from __future__ import annotations

import argparse
import os
import sys

from .usbfs import UsbfsDevice
from . import vli


def _eprint(*a, **kw):
    kw.setdefault("file", sys.stderr)
    print(*a, **kw)


def _read_sysfs_bcd(vid: int, pid: int) -> str | None:
    from .usbfs import find_device
    try:
        bus, dev = find_device(vid, pid)
    except RuntimeError:
        return None
    for name in os.listdir("/sys/bus/usb/devices"):
        base = f"/sys/bus/usb/devices/{name}"
        try:
            with open(f"{base}/busnum") as fh:
                b = int(fh.read())
            with open(f"{base}/devnum") as fh:
                d = int(fh.read())
        except OSError:
            continue
        if (b, d) == (bus, dev):
            try:
                with open(f"{base}/bcdDevice") as fh:
                    raw = fh.read().strip()
                return f"{int(raw[:2])}.{raw[2:]}"  # e.g. 0643 -> 6.43
            except OSError:
                return None
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vlidump",
                                description="Read the dock's VIA Labs USB hub")
    p.add_argument("--vidpid", default="2109:0822",
                   help="VID:PID of the hub (default 2109:0822 = VL822)")
    p.add_argument("--regs", help="comma-separated hex register addrs to read")
    p.add_argument("--spi-dump", metavar="FILE", help="dump SPI flash to FILE")
    p.add_argument("--spi-len", default="0x80000",
                   help="bytes to read for --spi-dump (default 0x80000 = 512K)")
    p.add_argument("--spi-chunk", default="64", help="bytes per SPI transfer")
    p.add_argument("--decode-fw", metavar="FILE",
                   help="offline: decode a VL8xx flash image (no hardware)")
    p.add_argument("--follow", metavar="FILE",
                   help="offline: recursive-follow an image's 8051 code, list functions")
    p.add_argument("--fndiff", metavar="FILE", nargs=2,
                   help="offline: function-level diff between two VL8xx images")
    args = p.parse_args(argv)

    if args.decode_fw:                       # offline, no device needed
        from . import fw
        with open(args.decode_fw, "rb") as fh:
            print(fw.render(fh.read()))
        return 0

    if args.follow:                          # offline recursive-follow summary
        from . import fw, trace
        blob = open(args.follow, "rb").read()
        c = fw.decode_container(blob)
        code = blob[c.code_start:]
        tr = trace.follow(code)
        hi = max(tr.insns) if tr.insns else 0
        print(f"{c.family} code@0x{c.code_start:04x}: entries={tr.entries} "
              f"funcs={len(tr.funcs)} insns={len(tr.insns)} "
              f"reachable={tr.reachable_bytes}B max=0x{hi:04x} jumptables={len(tr.indirect)}")
        print("function entries:", ", ".join(f"0x{a:04x}" for a in sorted(tr.funcs)))
        return 0

    if args.fndiff:                          # offline function-level diff
        from . import fw, trace
        import os.path
        codes = []
        for path in args.fndiff:
            blob = open(path, "rb").read()
            codes.append(blob[fw.decode_container(blob).code_start:])
        print(trace.format_fndiff(codes[0], codes[1],
                                  os.path.basename(args.fndiff[0]),
                                  os.path.basename(args.fndiff[1])))
        return 0

    vid, pid = (int(x, 16) for x in args.vidpid.split(":"))

    if os.geteuid() != 0:
        _eprint("note: usbfs access needs root -- try sudo")

    try:
        dev = UsbfsDevice.open_vidpid(vid, pid)
    except (RuntimeError, PermissionError, OSError) as e:
        _eprint(f"error opening {vid:04x}:{pid:04x}: {e}")
        return 2

    try:
        if args.regs:
            for tok in args.regs.split(","):
                addr = int(tok, 16)
                print(f"  0x{addr:04x} = 0x{vli.reg_read(dev, addr):02x}")
            return 0

        if args.spi_dump:
            length = int(args.spi_len, 0)
            chunk = int(args.spi_chunk, 0)
            _eprint(f"reading {length} bytes of SPI flash "
                    f"(opcode 0x{vli.SPI_READ_DATA:02x}, chunk {chunk}) ...")
            blob = vli.spi_read(dev, 0x0, length, chunk=chunk)
            with open(args.spi_dump, "wb") as fh:
                fh.write(blob)
            print(f"wrote {len(blob)} bytes -> {args.spi_dump}")
            return 0

        # default: identity
        info = vli.identify(dev)
        bcd = _read_sysfs_bcd(vid, pid)
        print(f"=== VIA Labs USB hub {vid:04x}:{pid:04x} ===")
        if bcd:
            print(f"fw (bcdDevice)  : {bcd}")
        for ln in info.lines():
            print(ln)
        try:
            jid = vli.spi_jedec_id(dev)
            print(f"flash JEDEC id  : {jid.hex(' ')}")
        except OSError as e:
            _eprint(f"(JEDEC read failed: {e})")
        return 0
    finally:
        dev.close()


if __name__ == "__main__":
    sys.exit(main())
