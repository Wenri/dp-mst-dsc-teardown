# SPDX-License-Identifier: WTFPL
"""Minimal usbfs transport: vendor control-IN transfers, stdlib only.

The VIA Labs hub on the USB half of the dock enumerates as an ordinary USB
device, so -- unlike the DP/AUX side (which needs NVIDIA's RM ioctl) -- we can
reach it straight through ``/dev/bus/usb/BBB/DDD`` with the ``USBDEVFS_CONTROL``
ioctl. ctypes + fcntl only, matching ``vmmdump``'s no-dependencies rule.

This module is deliberately **read-only**: it exposes ``control_in`` and nothing
that can write to the device. Vendor control reads to recipient=DEVICE go through
the default control endpoint and do *not* require claiming (or detaching the
kernel ``hub`` driver from) any interface, so dumping the hub does not disturb
the storage/network devices sitting behind it.
"""
from __future__ import annotations

import ctypes
import fcntl
import os


class _CtrlTransfer(ctypes.Structure):
    _fields_ = [
        ("bRequestType", ctypes.c_uint8),
        ("bRequest", ctypes.c_uint8),
        ("wValue", ctypes.c_uint16),
        ("wIndex", ctypes.c_uint16),
        ("wLength", ctypes.c_uint16),
        ("timeout", ctypes.c_uint32),  # milliseconds
        ("data", ctypes.c_void_p),
    ]


def _IOWR(type_: str, nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (ord(type_) << 8) | nr


USBDEVFS_CONTROL = _IOWR("U", 0, ctypes.sizeof(_CtrlTransfer))

# bmRequestType = direction(IN) | type(vendor) | recipient(device)
_DIR_IN = 0x80
_TYPE_VENDOR = 0x40
_RECIP_DEVICE = 0x00
REQTYPE_VENDOR_IN = _DIR_IN | _TYPE_VENDOR | _RECIP_DEVICE  # 0xC0


def find_device(vid: int, pid: int) -> tuple[int, int]:
    """Return ``(busnum, devnum)`` for the first sysfs USB device matching."""
    root = "/sys/bus/usb/devices"
    for name in sorted(os.listdir(root)):
        base = os.path.join(root, name)
        try:
            with open(os.path.join(base, "idVendor")) as fh:
                v = int(fh.read().strip(), 16)
            with open(os.path.join(base, "idProduct")) as fh:
                p = int(fh.read().strip(), 16)
        except (OSError, ValueError):
            continue
        if v == vid and p == pid:
            with open(os.path.join(base, "busnum")) as fh:
                bus = int(fh.read().strip())
            with open(os.path.join(base, "devnum")) as fh:
                dev = int(fh.read().strip())
            return bus, dev
    raise RuntimeError(f"no USB device {vid:04x}:{pid:04x} found")


class UsbfsDevice:
    """A USB device opened via usbfs for vendor control-IN transfers."""

    def __init__(self, busnum: int, devnum: int):
        self.path = f"/dev/bus/usb/{busnum:03d}/{devnum:03d}"
        self.fd = os.open(self.path, os.O_RDWR)

    @classmethod
    def open_vidpid(cls, vid: int, pid: int) -> "UsbfsDevice":
        return cls(*find_device(vid, pid))

    def control_in(self, b_request: int, w_value: int, w_index: int,
                   length: int, timeout_ms: int = 1000) -> bytes:
        buf = (ctypes.c_uint8 * length)()
        ct = _CtrlTransfer(
            bRequestType=REQTYPE_VENDOR_IN,
            bRequest=b_request & 0xFF,
            wValue=w_value & 0xFFFF,
            wIndex=w_index & 0xFFFF,
            wLength=length,
            timeout=timeout_ms,
            data=ctypes.cast(buf, ctypes.c_void_p),
        )
        n = fcntl.ioctl(self.fd, USBDEVFS_CONTROL, ct)
        return bytes(buf[:n])

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "UsbfsDevice":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
