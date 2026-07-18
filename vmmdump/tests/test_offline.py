# SPDX-License-Identifier: WTFPL
"""Offline regression tests: decode the committed dump.txt, no hardware needed.

Run directly (``python3 vmmdump/tests/test_offline.py``) or under pytest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from vmmdump import addresses, decode  # noqa: E402
from vmmdump.edid import scan_edids  # noqa: E402

DUMP = os.path.join(os.path.dirname(__file__), "..", "..", "dump.txt")


def _regs():
    return addresses.parse_registers(DUMP)


def test_register_count():
    regs = _regs()
    assert len(regs) == 2141, len(regs)
    assert regs[0x2107C0] == 0x32A          # link rate 810 = HBR3
    assert regs[0x220A00] == 0xA2000006     # RFRM0 DSC enabled


def test_decode_link_and_streams():
    d = decode.decode_all(_regs())
    assert d.rx_link.lanes_trained == 2
    assert d.rx_link.link_rate_code == 810
    assert d.mst_slots == 63
    assert d.fw_build_stamp == 0x20160729

    s0 = d.streams[0]
    assert (s0.timing.ha, s0.timing.va) == (1920, 1080)
    assert (s0.timing.ht, s0.timing.vt) == (2100, 1120)
    assert s0.dsc.enabled and s0.dsc.version == "1.2"
    assert abs(s0.dsc.bpp - 10.0) < 1e-6
    assert abs(s0.dsc.ratio - 2.4) < 1e-6

    assert not d.streams[1].active            # RFRM1 idle
    s2 = d.streams[2]
    assert (s2.timing.ha, s2.timing.va) == (2560, 1440)
    assert not s2.dsc.enabled


def test_tx_outputs():
    d = decode.decode_all(_regs())
    assert d.tx[0].kind == "HDMI"
    assert d.tx[1].kind == "idle"
    assert d.tx[2].kind == "DP" and d.tx[2].lanes == 4


class _StubAux:
    """Replays the pre-reboot baseline capture (vmm_baseline_slots.txt)."""

    def read_dpcd(self, addr, length):
        if addr == 0x100:                    # LINK_BW_SET / LANE_COUNT_SET
            return bytes([0x1E, 0x02])       # HBR3 x2
        if addr == 0x2C0:                    # VC payload table
            return bytes([3] * 48 + [4] * 12 + [0] * 3)
        raise AssertionError(f"unexpected DPCD read at {addr:#x}")


def test_slots_readout():
    from vmmdump.slots import read_slots, render_slots
    st = read_slots(_StubAux())
    assert st.bw_name == "HBR3" and st.lanes == 2
    assert round(st.data_gbps, 2) == 12.96
    assert round(st.slot_mbps, 1) == 202.5
    assert st.allocated == 60 and st.free == 3
    assert st.counts()[3] == 48 and st.counts()[4] == 12
    text = render_slots(st)
    assert "HBR3 8.10 Gb/s x 2 lane(s)" in text
    assert "allocated slots: 60/63   free: 3" in text
    assert "VC/stream id 3: 48 slots" in text


def test_edids_recovered():
    rows = addresses.parse_memory(DUMP)
    base = min(rows)
    blob = bytearray()
    for off in range(base, max(rows) + 16, 16):
        blob += rows.get(off, b"\x00" * 16)
    edids = scan_edids(bytes(blob), base)
    names = {e.name for e in edids}
    assert "D1" in names
    assert "VA3209-QHD" in names
    assert any(e.manufacturer == "SYN" for e in edids)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed")
