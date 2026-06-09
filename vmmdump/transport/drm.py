# SPDX-License-Identifier: WTFPL
"""DPCD transport over a ``/dev/drm_dp_aux*`` char device (amdgpu/i915/nouveau).

On these drivers the DPCD address space is simply the file offset, so a DPCD
read/write is a pread/pwrite. NVIDIA does not expose these nodes -- use the nvrm
backend there. Provided so the same tool works unchanged on non-NVIDIA hosts.
"""
from __future__ import annotations

import glob
import os

MAX_AUX_BURST = 16


class DrmTransport:
    name = "drm"

    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)

    def read_dpcd(self, addr: int, length: int) -> bytes:
        out = bytearray()
        while length:
            n = min(length, MAX_AUX_BURST)
            chunk = os.pread(self.fd, n, addr)
            if not chunk:
                break
            out += chunk
            addr += len(chunk)
            length -= len(chunk)
        return bytes(out)

    def write_dpcd(self, addr: int, data: bytes) -> None:
        i = 0
        while i < len(data):
            chunk = data[i:i + MAX_AUX_BURST]
            os.pwrite(self.fd, chunk, addr + i)
            i += len(chunk)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


def list_aux_devices() -> list[str]:
    return sorted(glob.glob("/dev/drm_dp_aux*"))
