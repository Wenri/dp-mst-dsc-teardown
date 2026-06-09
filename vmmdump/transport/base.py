# SPDX-License-Identifier: WTFPL
"""Transport interface shared by all AUX backends.

A transport is bound to a single DisplayPort sink/branch and exposes native
DPCD (AUX) reads/writes. Everything above it -- the Synaptics RC protocol, the
register dumper, the decoders -- is written against this interface and never
needs to know which GPU/driver actually moved the bytes.

Concrete backends:
  * ``vmmdump.transport.nvrm.NvRmTransport``  -- NVIDIA RM ioctl (NV0073 AUXCH)
  * ``vmmdump.transport.drm.DrmTransport``    -- /dev/drm_dp_aux* (amdgpu/i915/nouveau)

Implementations must transparently chunk requests longer than a single native
AUX transaction (16 bytes).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AuxTransport(Protocol):
    name: str

    def read_dpcd(self, addr: int, length: int) -> bytes:
        """Read ``length`` bytes of DPCD starting at 20-bit ``addr``."""
        ...

    def write_dpcd(self, addr: int, data: bytes) -> None:
        """Write ``data`` to DPCD starting at 20-bit ``addr``."""
        ...

    def close(self) -> None:
        ...
