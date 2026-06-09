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
