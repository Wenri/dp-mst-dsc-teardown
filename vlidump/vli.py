# SPDX-License-Identifier: WTFPL
"""VIA Labs (VLI) USB-hub register + SPI-flash read protocol.

The USB half of the dock is a VIA Labs hub (VID 0x2109). VLI hubs expose their
internal register space and external SPI flash through vendor control transfers
on the default endpoint. The request encoding here was reimplemented from the
protocol used by fwupd's ``plugins/vli`` (LGPL-2.1+, **reference only -- not
copied**), the same clean-room approach ``vmmdump/rc.py`` took for synaptics-mst.

Register read (1 byte), vendor control-IN:
    bRequest = addr >> 8 , wValue = addr & 0xff , wIndex = 0

SPI flash read, vendor control-IN, bRequest = 0xC4:
    wValue = ((addr >> 8) & 0xff00) | spi_opcode
    wIndex = ((addr << 8) & 0xff00) | ((addr >> 8) & 0x00ff)   # byteswap(addr16)
    -- i.e. the 24-bit flash address is split across wValue[15:8] and wIndex.

Only read opcodes are used (no flash write/erase): SPI READ_DATA (0x03),
READ_STATUS (0x05), JEDEC RDID (0x9F). Everything in this module is read-only.
"""
from __future__ import annotations

from dataclasses import dataclass

VID_VIA_LABS = 0x2109

# VLI internal registers that identify the silicon (from fwupd vli quirks).
REG_CHIP_VER = 0xF88C
REG_CHIP_VER2 = 0xF63F
REG_CHIP_ID1 = 0xF88E
REG_CHIP_ID2 = 0xF88F
REG_CHIP_ID1_ALT = 0xF64E
REG_CHIP_ID2_ALT = 0xF64F
REG_PACKAGE = 0xF651

# SPI flash opcodes (standard SPI-NOR; only reads issued here)
SPI_READ_DATA = 0x03
SPI_READ_STATUS = 0x05
SPI_RDID = 0x9F

# (chip_id1, chip_id2) -> friendly name. VL817 is documented by fwupd; the
# others are filled in by reading live silicon (this is a teardown, after all).
KNOWN_CHIPS = {
    (0x38, 0x35): "VL817",
}

REQ_SPI_READ = 0xC4
REQ_SPI_STATUS = 0xC1


def reg_read(dev, addr: int) -> int:
    """Read one internal register byte."""
    return dev.control_in(addr >> 8, addr & 0xFF, 0x0, 1)[0]


@dataclass
class ChipInfo:
    chip_ver: int
    chip_ver2: int
    chip_id1: int
    chip_id2: int
    chip_id1_alt: int
    chip_id2_alt: int
    package: int

    @property
    def name(self) -> str:
        return (KNOWN_CHIPS.get((self.chip_id1, self.chip_id2))
                or KNOWN_CHIPS.get((self.chip_id1_alt, self.chip_id2_alt))
                or f"VL?? (id {self.chip_id1:02x}{self.chip_id2:02x} / "
                   f"alt {self.chip_id1_alt:02x}{self.chip_id2_alt:02x})")

    def lines(self) -> list[str]:
        return [
            f"chip            : {self.name}",
            f"chip ver        : 0x{self.chip_ver:02x} / 0x{self.chip_ver2:02x}",
            f"chip id         : {self.chip_id1:02x} {self.chip_id2:02x}  "
            f"(alt {self.chip_id1_alt:02x} {self.chip_id2_alt:02x})",
            f"package         : 0x{self.package:02x}",
        ]


def identify(dev) -> ChipInfo:
    return ChipInfo(
        chip_ver=reg_read(dev, REG_CHIP_VER),
        chip_ver2=reg_read(dev, REG_CHIP_VER2),
        chip_id1=reg_read(dev, REG_CHIP_ID1),
        chip_id2=reg_read(dev, REG_CHIP_ID2),
        chip_id1_alt=reg_read(dev, REG_CHIP_ID1_ALT),
        chip_id2_alt=reg_read(dev, REG_CHIP_ID2_ALT),
        package=reg_read(dev, REG_PACKAGE),
    )


def spi_read(dev, addr: int, length: int, opcode: int = SPI_READ_DATA,
             chunk: int = 64) -> bytes:
    """Read ``length`` bytes from the hub's external SPI flash (read-only)."""
    out = bytearray()
    pos = addr
    remaining = length
    while remaining:
        n = min(remaining, chunk)
        w_value = ((pos >> 8) & 0xFF00) | (opcode & 0xFF)
        w_index = ((pos << 8) & 0xFF00) | ((pos >> 8) & 0x00FF)
        out += dev.control_in(REQ_SPI_READ, w_value, w_index, n)
        pos += n
        remaining -= n
    return bytes(out)


def spi_jedec_id(dev) -> bytes:
    """Best-effort flash JEDEC RDID (manufacturer + device, 3 bytes)."""
    return spi_read(dev, 0x0, 3, opcode=SPI_RDID, chunk=3)
