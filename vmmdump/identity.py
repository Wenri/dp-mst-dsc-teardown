# SPDX-License-Identifier: WTFPL
"""Chip / firmware identity, read the way fwupd's synaptics-mst setup() does."""
from __future__ import annotations

from dataclasses import dataclass

# direct DPCD (no RC needed)
REG_BRANCH_OUI = 0x500       # 3 bytes
REG_BRANCH_DEVID = 0x503     # 6 bytes ASCII
REG_HW_REV = 0x509           # 1 byte
REG_CHIP_ID = 0x507          # u16 BE
REG_FIRMWARE_VERSION = 0x50A  # 3 bytes: major, minor, vendor
RC_CAP = 0x4B0

SYNAPTICS_OUI = b"\x90\xcc\x24"

# board/customer id memory addresses by family (ADDR_MEMORY_*)
_BOARD_ID_ADDR = {
    "tesla": 0x170E, "leaf": 0x170E, "panamera": 0x170E,
    "cayenne": 0x9000024E, "spyder": 0x9000020E,
}


def family_from_chip_id(chip_id: int) -> str:
    if 0x9000 <= chip_id < 0xA000:
        return "carrera"
    if 0x7000 <= chip_id < 0x8000:
        return "spyder"
    if (0x6000 <= chip_id < 0x7000) or (0x8000 <= chip_id < 0x9000):
        return "cayenne"
    if 0x5000 <= chip_id < 0x6000:
        return "panamera"
    if 0x3000 <= chip_id < 0x4000:
        return "leaf"
    if 0x2000 <= chip_id < 0x3000:
        return "tesla"
    return "unknown"


@dataclass
class Identity:
    oui: bytes
    is_synaptics: bool
    branch_devid: bytes
    chip_id: int
    chip_name: str
    family: str
    hw_rev: int
    fw_version: str
    rc_cap: int
    rc_supported: bool
    board_id: int | None = None
    customer_id: int | None = None

    def lines(self) -> list[str]:
        out = [
            f"CHIP ID         : {self.chip_name}",
            f"CHIP family     : {self.family.capitalize()}",
            f"Firmware version: {self.fw_version}",
            f"Branch OUI      : {self.oui.hex(':')}"
            + ("  (Synaptics)" if self.is_synaptics else ""),
            f"Branch dev id   : {self.branch_devid.decode('ascii', 'replace').rstrip(chr(0))!r}",
            f"HW revision     : 0x{self.hw_rev:02x}",
            f"RC capability   : 0x{self.rc_cap:02x}"
            + ("  (remote-control supported)" if self.rc_supported else ""),
        ]
        if self.board_id is not None:
            out.append(f"Board ID        : 0x{self.board_id:04x} ({self.board_id})")
        if self.customer_id is not None:
            out.append(f"Customer ID     : 0x{self.customer_id:04x} ({self.customer_id})")
        return out


def read_identity(transport, rc=None) -> Identity:
    """Read identity over ``transport`` (DPCD). ``rc`` (enabled) adds board id."""
    oui = transport.read_dpcd(REG_BRANCH_OUI, 3)
    devid = transport.read_dpcd(REG_BRANCH_DEVID, 6)
    hw_rev = transport.read_dpcd(REG_HW_REV, 1)[0]
    cid = transport.read_dpcd(REG_CHIP_ID, 2)
    chip_id = int.from_bytes(cid, "big")
    ver = transport.read_dpcd(REG_FIRMWARE_VERSION, 3)
    cap = transport.read_dpcd(RC_CAP, 1)[0]
    family = family_from_chip_id(chip_id)

    ident = Identity(
        oui=oui, is_synaptics=oui == SYNAPTICS_OUI, branch_devid=devid,
        chip_id=chip_id, chip_name=f"VMM{chip_id:04X}", family=family,
        hw_rev=hw_rev, fw_version=f"{ver[0]}.{ver[1]:02d}.{ver[2]}",
        rc_cap=cap, rc_supported=bool(cap & 0x04),
    )

    if rc is not None and family in _BOARD_ID_ADDR:
        try:
            raw = rc.read_memory(_BOARD_ID_ADDR[family], 4)
            ident.board_id = int.from_bytes(raw[0:2], "big")
            ident.customer_id = int.from_bytes(raw[2:4], "big")
        except Exception:
            pass
    return ident
