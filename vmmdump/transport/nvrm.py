#!/usr/bin/env python3
# SPDX-License-Identifier: WTFPL
"""NVIDIA Resource-Manager DisplayPort AUX backend.

Issues native DP AUX (DPCD) transactions through the NVIDIA kernel driver's
ioctl ABI on ``/dev/nvidiactl`` + ``/dev/nvidia0`` -- the same path
``nvidia-modeset`` uses internally (``nvkms-rm.c: ReadDPCDReg``).

This exists because the NVIDIA driver (proprietary *and* open-gpu-kernel-modules)
does NOT register the standard ``/dev/drm_dp_aux*`` char device that amdgpu/i915/
nouveau expose, so the usual fwupd-style path cannot reach a DP MST hub wired to
an NVIDIA GPU. The RM control ``NV0073_CTRL_CMD_DP_AUXCH_CTRL`` does the same job
and is flagged ``RMCTRL_FLAGS_PRIVILEGED`` -- callable from a root userspace client.

ABI constants and struct layouts are taken verbatim from NVIDIA's
open-gpu-kernel-modules SDK headers (MIT-licensed):
  * src/common/sdk/nvidia/inc/nvos.h                      (NVOS21/NVOS54)
  * src/common/sdk/nvidia/inc/class/cl{0000,0080,2080,0073}.h
  * src/common/sdk/nvidia/inc/ctrl/ctrl0000/ctrl0000gpu.h (GET_ID_INFO_V2)
  * src/common/sdk/nvidia/inc/ctrl/ctrl0073/ctrl0073{system,dp}.h
  * src/nvidia/arch/nvalloc/unix/include/nv_escape.h      (NV_ESC_RM_*)
  * kernel-open/common/inc/nv-ioctl{,-numbers}.h          (CARD_INFO/REGISTER_FD)
The open<->client handshake mirrors tinygrad's pure-python ops_nv.py.

Run directly as a probe:
    sudo python3 vmmdump/transport/nvrm.py [--verbose]
"""
from __future__ import annotations

import ctypes
import fcntl
import os
import sys

# --------------------------------------------------------------------------- #
# ioctl plumbing
# --------------------------------------------------------------------------- #
NV_IOCTL_MAGIC = ord("F")
NV_IOCTL_BASE = 200
NV_ESC_CARD_INFO = NV_IOCTL_BASE + 0       # 200
NV_ESC_REGISTER_FD = NV_IOCTL_BASE + 1     # 201
NV_ESC_RM_FREE = 0x29
NV_ESC_RM_CONTROL = 0x2A
NV_ESC_RM_ALLOC = 0x2B

# classes
NV01_ROOT_CLIENT = 0x41
NV01_DEVICE_0 = 0x80
NV20_SUBDEVICE_0 = 0x2080
NV04_DISPLAY_COMMON = 0x73

# controls
NV0000_CTRL_CMD_GPU_GET_ID_INFO_V2 = 0x205
NV0073_CTRL_CMD_SYSTEM_GET_SUPPORTED = 0x730107
NV0073_CTRL_CMD_SYSTEM_GET_CONNECT_STATE = 0x730108
NV0073_CTRL_CMD_DP_AUXCH_CTRL = 0x731341

# AUXCH cmd bitfields (ctrl0073dp.h)
AUXCH_CMD_TYPE_AUX = 1        # bit 3
AUXCH_CMD_REQ_READ = 1        # bits 1:0
AUXCH_CMD_REQ_WRITE = 0
AUXCH_REPLYTYPE_ACK = 0
AUXCH_MAX_DATA_SIZE = 16
_AUXCH_REPLY_STR = {0: "ACK", 1: "NACK", 2: "DEFER", 3: "TIMEOUT",
                    4: "I2CNACK", 8: "I2CDEFER", 0xFFFFFFFF: "INVALID_ARGUMENT"}


def _IOWR(nr: int, size: int) -> int:
    # dir(2)<<30 | size(14)<<16 | type(8)<<8 | nr(8); dir READ|WRITE = 3
    return (3 << 30) | ((size & 0x3FFF) << 16) | (NV_IOCTL_MAGIC << 8) | (nr & 0xFF)


NvHandle = ctypes.c_uint32
NvU32 = ctypes.c_uint32
NvS32 = ctypes.c_int32
NvU16 = ctypes.c_uint16
NvU8 = ctypes.c_uint8
NvP64 = ctypes.c_uint64  # 8-byte handle/pointer


class NVOS21_PARAMETERS(ctypes.Structure):
    _fields_ = [("hRoot", NvHandle), ("hObjectParent", NvHandle),
                ("hObjectNew", NvHandle), ("hClass", ctypes.c_int32),
                ("pAllocParms", NvP64), ("paramsSize", NvU32),
                ("status", ctypes.c_int32)]


class NVOS54_PARAMETERS(ctypes.Structure):
    _fields_ = [("hClient", NvHandle), ("hObject", NvHandle),
                ("cmd", ctypes.c_int32), ("flags", NvU32),
                ("params", NvP64), ("paramsSize", NvU32),
                ("status", ctypes.c_int32)]


class NV0080_ALLOC_PARAMETERS(ctypes.Structure):
    _fields_ = [("deviceId", NvU32), ("hClientShare", NvHandle),
                ("hTargetClient", NvHandle), ("hTargetDevice", NvHandle),
                ("flags", NvU32), ("vaSpaceSize", ctypes.c_uint64),
                ("vaStartInternal", ctypes.c_uint64),
                ("vaLimitInternal", ctypes.c_uint64), ("vaMode", NvU32)]


class NV2080_ALLOC_PARAMETERS(ctypes.Structure):
    _fields_ = [("subDeviceId", NvU32)]


class NV0000_CTRL_GPU_GET_ID_INFO_V2_PARAMS(ctypes.Structure):
    _fields_ = [("gpuId", NvU32), ("gpuFlags", NvU32),
                ("deviceInstance", NvU32), ("subDeviceInstance", NvU32),
                ("sliStatus", NvU32), ("boardId", NvU32),
                ("gpuInstance", NvU32), ("numaId", NvS32)]


class NV0073_CTRL_SYSTEM_GET_SUPPORTED_PARAMS(ctypes.Structure):
    _fields_ = [("subDeviceInstance", NvU32), ("displayMask", NvU32),
                ("displayMaskDDC", NvU32)]


class NV0073_CTRL_SYSTEM_GET_CONNECT_STATE_PARAMS(ctypes.Structure):
    _fields_ = [("subDeviceInstance", NvU32), ("flags", NvU32),
                ("displayMask", NvU32), ("retryTimeMs", NvU32)]


class NV0073_CTRL_DP_AUXCH_CTRL_PARAMS(ctypes.Structure):
    _fields_ = [("subDeviceInstance", NvU32), ("displayId", NvU32),
                ("bAddrOnly", NvU8), ("cmd", NvU32), ("addr", NvU32),
                ("data", NvU8 * AUXCH_MAX_DATA_SIZE), ("size", NvU32),
                ("replyType", NvU32), ("retryTimeMs", NvU32)]


class nv_pci_info_t(ctypes.Structure):
    _fields_ = [("domain", NvU32), ("bus", NvU8), ("slot", NvU8),
                ("function", NvU8), ("vendor_id", NvU16), ("device_id", NvU16)]


class nv_ioctl_card_info_t(ctypes.Structure):
    _fields_ = [("valid", NvU8), ("pci_info", nv_pci_info_t),
                ("gpu_id", NvU32), ("interrupt_line", NvU16),
                ("reg_address", ctypes.c_uint64), ("reg_size", ctypes.c_uint64),
                ("fb_address", ctypes.c_uint64), ("fb_size", ctypes.c_uint64),
                ("minor_number", NvU32), ("dev_name", NvU8 * 10)]


class nv_ioctl_register_fd_t(ctypes.Structure):
    _fields_ = [("ctl_fd", ctypes.c_int)]


class NvRmError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# RM client
# --------------------------------------------------------------------------- #
class NvRmClient:
    """A root RM client able to issue NV0073 display controls on one GPU."""

    def __init__(self, gpu_index: int = 0, verbose: bool = False):
        self.verbose = verbose
        self._dev_fds: list[int] = []
        self.fd_ctl = os.open("/dev/nvidiactl", os.O_RDWR | os.O_CLOEXEC)
        # 1. root client
        self.root = self._rm_alloc(0, NV01_ROOT_CLIENT, None, root=0)
        # 2. enumerate GPUs
        cards = (nv_ioctl_card_info_t * 64)()
        self._ioctl(self.fd_ctl, NV_ESC_CARD_INFO, cards)
        gpus = [c for c in cards if c.valid]
        if not gpus:
            raise NvRmError("no NVIDIA GPUs reported by CARD_INFO")
        if gpu_index >= len(gpus):
            raise NvRmError(f"gpu index {gpu_index} >= {len(gpus)} present")
        card = gpus[gpu_index]
        self.gpu_id = card.gpu_id
        # 3. gpu instance for device alloc
        idinfo = NV0000_CTRL_GPU_GET_ID_INFO_V2_PARAMS(gpuId=card.gpu_id)
        self._rm_control(self.root, NV0000_CTRL_CMD_GPU_GET_ID_INFO_V2, idinfo)
        self.device_instance = idinfo.deviceInstance
        # 4. attach the per-GPU node to this client
        fd_dev = os.open(f"/dev/nvidia{card.minor_number}", os.O_RDWR | os.O_CLOEXEC)
        self._dev_fds.append(fd_dev)
        self._ioctl(fd_dev, NV_ESC_REGISTER_FD,
                    nv_ioctl_register_fd_t(ctl_fd=self.fd_ctl))
        # 5. device -> subdevice -> display-common
        self.hDevice = self._rm_alloc(
            self.root, NV01_DEVICE_0,
            NV0080_ALLOC_PARAMETERS(deviceId=self.device_instance,
                                    hClientShare=self.root))
        self.hSubDevice = self._rm_alloc(
            self.hDevice, NV20_SUBDEVICE_0, NV2080_ALLOC_PARAMETERS(subDeviceId=0))
        self.hDisp = self._rm_alloc(self.hDevice, NV04_DISPLAY_COMMON, None)
        if self.verbose:
            print(f"[nvrm] gpu_id=0x{self.gpu_id:08x} devInst={self.device_instance} "
                  f"root=0x{self.root:08x} disp=0x{self.hDisp:08x}", file=sys.stderr)

    # -- ioctl helpers ----------------------------------------------------- #
    def _ioctl(self, fd: int, nr: int, arg: ctypes.Structure) -> None:
        req = _IOWR(nr, ctypes.sizeof(arg))
        fcntl.ioctl(fd, req, arg, True)

    def _rm_alloc(self, parent, clss, params, root=None) -> int:
        p = NVOS21_PARAMETERS(
            hRoot=self.root if root is None else root,
            hObjectParent=parent, hObjectNew=0, hClass=clss,
            pAllocParms=ctypes.cast(ctypes.byref(params), ctypes.c_void_p).value if params else 0,
            paramsSize=ctypes.sizeof(params) if params else 0)
        self._ioctl(self.fd_ctl, NV_ESC_RM_ALLOC, p)
        if p.status != 0:
            raise NvRmError(f"rm_alloc(class=0x{clss:x}) status=0x{p.status & 0xffffffff:08x}")
        return p.hObjectNew

    def _rm_control(self, obj, cmd, params) -> None:
        p = NVOS54_PARAMETERS(
            hClient=self.root, hObject=obj, cmd=cmd, flags=0,
            params=ctypes.cast(ctypes.byref(params), ctypes.c_void_p).value if params else 0,
            paramsSize=ctypes.sizeof(params) if params else 0)
        self._ioctl(self.fd_ctl, NV_ESC_RM_CONTROL, p)
        if p.status != 0:
            raise NvRmError(f"rm_control(cmd=0x{cmd:x}) status=0x{p.status & 0xffffffff:08x}")

    # -- display helpers --------------------------------------------------- #
    def supported_display_mask(self) -> int:
        p = NV0073_CTRL_SYSTEM_GET_SUPPORTED_PARAMS(subDeviceInstance=0)
        self._rm_control(self.hDisp, NV0073_CTRL_CMD_SYSTEM_GET_SUPPORTED, p)
        return p.displayMask

    def connected_display_mask(self, supported: int) -> int:
        p = NV0073_CTRL_SYSTEM_GET_CONNECT_STATE_PARAMS(
            subDeviceInstance=0, flags=0, displayMask=supported)
        self._rm_control(self.hDisp, NV0073_CTRL_CMD_SYSTEM_GET_CONNECT_STATE, p)
        return p.displayMask

    def aux_read(self, display_id: int, addr: int, length: int) -> bytes:
        """Native AUX (DPCD) read of ``length`` (<=16) bytes at ``addr``."""
        if not 1 <= length <= AUXCH_MAX_DATA_SIZE:
            raise ValueError("length must be 1..16")
        p = NV0073_CTRL_DP_AUXCH_CTRL_PARAMS(
            subDeviceInstance=0, displayId=display_id, bAddrOnly=0,
            cmd=(AUXCH_CMD_TYPE_AUX << 3) | AUXCH_CMD_REQ_READ,
            addr=addr, size=length - 1)
        self._rm_control(self.hDisp, NV0073_CTRL_CMD_DP_AUXCH_CTRL, p)
        if p.replyType != AUXCH_REPLYTYPE_ACK:
            raise NvRmError(f"aux_read(0x{addr:x}) reply="
                            f"{_AUXCH_REPLY_STR.get(p.replyType, p.replyType)}")
        return bytes(p.data[:p.size])

    def aux_write(self, display_id: int, addr: int, data: bytes) -> int:
        if not 1 <= len(data) <= AUXCH_MAX_DATA_SIZE:
            raise ValueError("data must be 1..16 bytes")
        p = NV0073_CTRL_DP_AUXCH_CTRL_PARAMS(
            subDeviceInstance=0, displayId=display_id, bAddrOnly=0,
            cmd=(AUXCH_CMD_TYPE_AUX << 3) | AUXCH_CMD_REQ_WRITE,
            addr=addr, size=len(data) - 1)
        for i, b in enumerate(data):
            p.data[i] = b
        self._rm_control(self.hDisp, NV0073_CTRL_CMD_DP_AUXCH_CTRL, p)
        if p.replyType != AUXCH_REPLYTYPE_ACK:
            raise NvRmError(f"aux_write(0x{addr:x}) reply="
                            f"{_AUXCH_REPLY_STR.get(p.replyType, p.replyType)}")
        return p.size

    def close(self) -> None:
        for fd in self._dev_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.close(self.fd_ctl)
        except OSError:
            pass

    def bind(self, display_id: int) -> "NvRmTransport":
        return NvRmTransport(self, display_id)


class NvRmTransport:
    """AuxTransport bound to one display: DPCD reads/writes auto-chunked to <=16B.

    Native AUX caps each transaction at NV0073_CTRL_DP_AUXCH_MAX_DATA_SIZE (16);
    larger DPCD ranges (e.g. the 32-byte RC data window) are split transparently,
    exactly as the kernel drm_dp_aux path does for fwupd.
    """

    name = "nvrm"

    def __init__(self, client: NvRmClient, display_id: int):
        self.client = client
        self.display_id = display_id

    def read_dpcd(self, addr: int, length: int) -> bytes:
        out = bytearray()
        while length:
            n = min(length, AUXCH_MAX_DATA_SIZE)
            out += self.client.aux_read(self.display_id, addr, n)
            addr += n
            length -= n
        return bytes(out)

    def write_dpcd(self, addr: int, data: bytes) -> None:
        i = 0
        while i < len(data):
            chunk = data[i:i + AUXCH_MAX_DATA_SIZE]
            self.client.aux_write(self.display_id, addr + i, chunk)
            i += len(chunk)

    def close(self) -> None:
        self.client.close()


# --------------------------------------------------------------------------- #
# Phase-0 probe
# --------------------------------------------------------------------------- #
def _iter_display_ids(mask: int):
    bit = 0
    while mask:
        if mask & 1:
            yield 1 << bit
        mask >>= 1
        bit += 1


def probe(verbose: bool = False) -> int:
    if os.geteuid() != 0:
        print("warning: not root; NV0073 AUXCH is PRIVILEGED and will likely "
              "fail with INSUFFICIENT_PERMISSIONS", file=sys.stderr)
    try:
        cli = NvRmClient(verbose=verbose)
    except (OSError, NvRmError) as e:
        print(f"FAIL: could not init RM client: {e}", file=sys.stderr)
        return 2

    found = False
    try:
        supported = cli.supported_display_mask()
        connected = cli.connected_display_mask(supported)
        print(f"supported displayMask=0x{supported:08x}  "
              f"connected=0x{connected:08x}")
        for did in _iter_display_ids(supported):
            tag = "connected" if (did & connected) else "disconnected"
            try:
                oui = cli.aux_read(did, 0x500, 3)
            except NvRmError as e:
                print(f"  displayId 0x{did:08x} [{tag}]: AUX 0x500 -> {e}")
                continue
            line = f"  displayId 0x{did:08x} [{tag}]: OUI={oui.hex(' ')}"
            is_syn = oui[:2] == b"\x90\xcc"
            try:
                cid = cli.aux_read(did, 0x507, 2)
                chip = int.from_bytes(cid, "big")
                line += f"  chip_id=0x{chip:04x}"
            except NvRmError:
                chip = 0
            try:
                cap = cli.aux_read(did, 0x4B0, 1)
                line += f"  RC_CAP=0x{cap[0]:02x}"
            except NvRmError:
                pass
            if is_syn:
                line += "  <-- Synaptics MST hub"
                found = True
            print(line)
    finally:
        cli.close()

    if found:
        print("\nPASS: reached a Synaptics MST branch over NVIDIA RM AUX.")
        return 0
    print("\nNo Synaptics OUI (90 CC) seen on any display. Is the dock attached "
          "to this GPU?", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(probe(verbose="--verbose" in sys.argv or "-v" in sys.argv))
