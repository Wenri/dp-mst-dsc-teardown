# SPDX-License-Identifier: WTFPL
"""Synaptics/Kinetic "Remote Control" (RC) protocol over DPCD.

VMM53xx (and the wider Synaptics MST family) expose their internal register and
memory space through a small command interface in DPCD AUX space. This is the
mechanism MegaChips' VMMTool and fwupd's synaptics-mst plugin both use; we
reimplement the read side here on top of an :class:`AuxTransport`.

Register map (DPCD byte addresses) and opcodes are from fwupd's
``plugins/synaptics-mst`` (LGPL-2.1+, reference only -- reimplemented, not copied):

    RC_CAP    0x4B0   bit 0x04 => RC supported
    RC_STATE  0x4B1
    RC_CMD    0x4B2   write (opcode|0x80) to trigger; poll until bit7 clears
    RC_RESULT 0x4B3   result byte (0 = success), read as RC_CMD+1
    RC_LEN    0x4B8   u32 LE
    RC_OFFSET 0x4BC   u32 LE   (arbitrary 32-bit chip address)
    RC_DATA   0x4C0   data window

The command interface must be unlocked first: ``enable()`` issues DISABLE_RC then
ENABLE_RC with the literal payload ``b"PRIUS"`` (exactly as fwupd's enable_rc).
Until then ReadFromMemory returns rc=Disabled(4).

Write path (rc_set_command): write RC_DATA, RC_OFFSET, RC_LEN, then trigger RC_CMD.
Read path  (rc_get_command): write RC_OFFSET, RC_LEN, trigger RC_CMD, read RC_DATA.
We only ever issue non-flash opcodes (enable/disable + ReadFromMemory/ReadFromTxDpcd).
"""
from __future__ import annotations

import struct
import time

# DPCD register addresses
RC_CAP = 0x4B0
RC_STATE = 0x4B1
RC_CMD = 0x4B2
RC_RESULT = 0x4B3
RC_LEN = 0x4B8
RC_OFFSET = 0x4BC
RC_DATA = 0x4C0

# opcodes (FuSynapticsMstUpdcCmd)
CMD_ENABLE_RC = 0x01
CMD_DISABLE_RC = 0x02
CMD_GET_ID = 0x03
CMD_GET_VERSION = 0x04
CMD_READ_FROM_EEPROM = 0x30
CMD_READ_FROM_MEMORY = 0x31
CMD_READ_FROM_TX_DPCD = 0x32  # TX0; TX1/2/3 = 0x33/0x34/0x35

CMD_ACTIVE = 0x80
UNIT_SIZE = 16  # bytes per RC data transaction (<= native AUX max)
ENABLE_PAYLOAD = b"PRIUS"

# result codes (FuSynapticsMstUpdcRc)
RC_SUCCESS = 0
RC_DISABLED = 4
_RC_STR = {0: "success", 1: "invalid", 2: "unsupported", 3: "failed",
           4: "disabled", 5: "configure-sign-failed", 6: "firmware-sign-failed",
           7: "rollback-failed"}


class RcError(RuntimeError):
    def __init__(self, msg: str, rc: int | None = None):
        super().__init__(msg)
        self.rc = rc


class SynapticsRC:
    def __init__(self, transport, retries: int = 30, delay: float = 0.1):
        self.t = transport
        self.retries = retries
        self.delay = delay

    # -- capability -------------------------------------------------------- #
    def cap(self) -> int:
        return self.t.read_dpcd(RC_CAP, 1)[0]

    def supported(self) -> bool:
        return bool(self.cap() & 0x04)

    # -- command primitive ------------------------------------------------- #
    def _trigger(self, opcode: int) -> int:
        """Write opcode|0x80, poll until bit7 clears, return the result byte."""
        self.t.write_dpcd(RC_CMD, bytes([opcode | CMD_ACTIVE]))
        for _ in range(self.retries):
            buf = self.t.read_dpcd(RC_CMD, 2)  # [cmd, result]
            if not (buf[0] & CMD_ACTIVE):
                return buf[1]
            time.sleep(self.delay)
        raise RcError(f"RC cmd 0x{opcode:02x} timed out (still active)")

    def _set_command(self, opcode: int, offset: int, data: bytes) -> int:
        """Write path: push data/offset/len then trigger. Returns result code."""
        if not data:
            return self._trigger(opcode)
        pos = offset
        for i in range(0, len(data), UNIT_SIZE):
            chunk = data[i:i + UNIT_SIZE]
            self.t.write_dpcd(RC_DATA, chunk)
            self.t.write_dpcd(RC_OFFSET, struct.pack("<I", pos))
            self.t.write_dpcd(RC_LEN, struct.pack("<I", len(chunk)))
            rc = self._trigger(opcode)
            if rc != RC_SUCCESS:
                return rc
            pos += len(chunk)
        return RC_SUCCESS

    def _get_command(self, opcode: int, offset: int, length: int,
                     unit: int = UNIT_SIZE) -> bytes:
        """Read path: push offset/len, trigger, read back data, per chunk.

        ``unit`` is the bytes requested per RC trigger; the transport still
        chunks each into <=16-byte AUX reads. Defaults to the conservative 16;
        bulk readers (EEPROM) may pass a larger window to cut the trigger count.
        """
        out = bytearray()
        pos = offset
        remaining = length
        while remaining:
            n = min(remaining, unit)
            self.t.write_dpcd(RC_OFFSET, struct.pack("<I", pos))
            self.t.write_dpcd(RC_LEN, struct.pack("<I", n))
            rc = self._trigger(opcode)
            if rc != RC_SUCCESS:
                raise RcError(f"RC read 0x{opcode:02x} @0x{pos:08x} -> "
                              f"{_RC_STR.get(rc, rc)}", rc)
            out += self.t.read_dpcd(RC_DATA, n)
            pos += n
            remaining -= n
        return bytes(out)

    # -- session ----------------------------------------------------------- #
    def enable(self) -> None:
        self.disable()  # toggle; a fresh chip answers DISABLE_RC with "disabled"
        rc = self._set_command(CMD_ENABLE_RC, 0, ENABLE_PAYLOAD)
        if rc != RC_SUCCESS:
            raise RcError(f"enable RC -> {_RC_STR.get(rc, rc)}", rc)

    def disable(self) -> None:
        rc = self._trigger(CMD_DISABLE_RC)
        if rc not in (RC_SUCCESS, RC_DISABLED):
            raise RcError(f"disable RC -> {_RC_STR.get(rc, rc)}", rc)

    def __enter__(self) -> "SynapticsRC":
        self.enable()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.disable()
        except RcError:
            pass

    # -- reads ------------------------------------------------------------- #
    def read_memory(self, addr: int, length: int) -> bytes:
        """Read ``length`` bytes from chip memory/register space at ``addr``."""
        return self._get_command(CMD_READ_FROM_MEMORY, addr, length)

    def read_tx_dpcd(self, tx: int, addr: int, length: int) -> bytes:
        """Read a downstream TX port's DPCD (tx 0..3) -- e.g. monitor EDID side."""
        if not 0 <= tx <= 3:
            raise ValueError("tx must be 0..3")
        return self._get_command(CMD_READ_FROM_TX_DPCD + tx, addr, length)

    def read_eeprom(self, addr: int, length: int, unit: int = 64) -> bytes:
        """Read the external SPI EEPROM/flash via RC ReadFromEeprom (0x30).

        READ-ONLY: same get-command framing as read_memory, never a flash write.
        ``unit`` is the bytes per RC trigger (transport chunks each to <=16-byte
        AUX); 64 keeps a full-image read tractable.

        Caveat on Panamera (VMM53xx): while the on-chip ESM (the firmware MCU) is
        running it owns the SPI bus, so these reads SUCCEED but return all-zero.
        Real flash bytes require disabling the ESM first (REG_ESM_DISABLE 0x2000fc
        + QUAD/HDCP off, then a reset to recover) -- a disruptive register-write
        sequence that blanks the live link. This read-only tool does NOT do that;
        on a live hub treat a zero result as "MCU holds the flash", not empty.
        """
        return self._get_command(CMD_READ_FROM_EEPROM, addr, length, unit)

    def read_reg32(self, addr: int) -> int:
        """Read one 32-bit register, little-endian (dump.txt convention)."""
        return struct.unpack("<I", self.read_memory(addr, 4))[0]
