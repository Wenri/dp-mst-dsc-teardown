# SPDX-License-Identifier: WTFPL
"""MST trunk + VC Payload ID table readout (native DPCD, read-only, no RC).

DP 1.4a puts the branch device's payload allocation at DPCD 0x2C0..0x2FE
(``VC_PAYLOAD_ID_SLOT_1..63``): one byte per time slot holding the VC/stream
id it carries, 0 = unallocated. Slot 0 of the 64-slot MTP is the header, so
63 are allocatable. 0x100/0x101 (``LINK_BW_SET``/``LANE_COUNT_SET``) hold the
trunk link the source programmed; with 8b/10b coding each slot is worth
``rate * lanes * 0.8 / 64`` of payload bandwidth.

This is how the D1 DSC experiment was measured: at 10 bpp the D1 stream held
12 slots; the 11 bpp driver patch moved it to 14 and (with the forced 10-bit
decode cap) let the 10-bit ViewSonic stream compress from 48 slots to 18.
"""
from __future__ import annotations

from dataclasses import dataclass

DPCD_LINK_BW_SET = 0x100
DPCD_PAYLOAD_TABLE = 0x2C0
PAYLOAD_SLOTS = 63

_LINK_BW = {0x06: ("RBR", 1.62), 0x0A: ("HBR", 2.70),
            0x14: ("HBR2", 5.40), 0x1E: ("HBR3", 8.10)}


@dataclass
class SlotTable:
    bw_code: int          # raw LINK_BW_SET
    bw_name: str          # "HBR3" / "0x??"
    lane_gbps: float      # per-lane line rate, 0 if unknown code
    lanes: int
    table: bytes          # 63 bytes, one VC id per slot

    @property
    def data_gbps(self) -> float:
        """Trunk payload bandwidth after 8b/10b."""
        return self.lane_gbps * self.lanes * 0.8

    @property
    def slot_mbps(self) -> float:
        return self.data_gbps * 1000 / 64

    def counts(self) -> dict[int, int]:
        c: dict[int, int] = {}
        for b in self.table:
            c[b] = c.get(b, 0) + 1
        return c

    @property
    def free(self) -> int:
        return self.counts().get(0, 0)

    @property
    def allocated(self) -> int:
        return PAYLOAD_SLOTS - self.free


def read_slots(transport) -> SlotTable:
    """Read trunk config + payload table over native DPCD (no RC session)."""
    link = transport.read_dpcd(DPCD_LINK_BW_SET, 2)
    name, gbps = _LINK_BW.get(link[0], (f"0x{link[0]:02x}", 0.0))
    return SlotTable(bw_code=link[0], bw_name=name, lane_gbps=gbps,
                     lanes=link[1] & 0x1F,
                     table=transport.read_dpcd(DPCD_PAYLOAD_TABLE, PAYLOAD_SLOTS))


def render_slots(st: SlotTable) -> str:
    out = [f"TRUNK: {st.bw_name} {st.lane_gbps:.2f} Gb/s x {st.lanes} lane(s)"
           f"  = {st.data_gbps:.2f} Gb/s data (8b/10b)",
           f"per MST time slot: {st.slot_mbps:.1f} Mb/s   (64-slot MTP, 63 usable)",
           "",
           f"VC Payload ID table (0x{DPCD_PAYLOAD_TABLE:X}.."
           f"0x{DPCD_PAYLOAD_TABLE + PAYLOAD_SLOTS - 1:X}), {PAYLOAD_SLOTS} slots:",
           "  raw: " + " ".join(f"{b:02x}" for b in st.table),
           "",
           f"  allocated slots: {st.allocated}/{PAYLOAD_SLOTS}   free: {st.free}"]
    for vc, n in sorted(st.counts().items()):
        label = "UNALLOCATED" if vc == 0 else f"VC/stream id {vc}"
        out.append(f"    {label}: {n} slots  (~{n * st.slot_mbps / 1000:.2f} Gb/s)")
    return "\n".join(out)
