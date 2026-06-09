# SPDX-License-Identifier: WTFPL
"""Find the transport+sink that reaches the Synaptics MST hub.

Tries the NVIDIA RM backend (the path for a dock wired to an NVIDIA GPU) and the
generic ``/dev/drm_dp_aux*`` backend, probing each candidate sink for the
Synaptics branch OUI (90 CC) at DPCD 0x500 and the RC capability bit at 0x4B0.
"""
from __future__ import annotations

from dataclasses import dataclass

from .identity import REG_BRANCH_OUI, RC_CAP, REG_CHIP_ID, SYNAPTICS_OUI


@dataclass
class Candidate:
    transport: object
    label: str
    oui: bytes
    chip_id: int
    rc_cap: int
    is_hub: bool
    _owner: object = None  # client/handle to close

    def score(self) -> int:
        s = 0
        if self.oui == SYNAPTICS_OUI:
            s += 5
        if 0x5000 <= self.chip_id < 0x6000:
            s += 3
        if self.rc_cap & 0x04:
            s += 3
        return s


def _probe(transport, label: str, owner=None) -> Candidate | None:
    try:
        oui = transport.read_dpcd(REG_BRANCH_OUI, 3)
        chip = int.from_bytes(transport.read_dpcd(REG_CHIP_ID, 2), "big")
        cap = transport.read_dpcd(RC_CAP, 1)[0]
    except Exception:
        return None
    is_hub = oui == SYNAPTICS_OUI and bool(cap & 0x04)
    return Candidate(transport, label, oui, chip, cap, is_hub, owner)


def _display_ids(mask: int):
    bit = 0
    while mask:
        if mask & 1:
            yield 1 << bit
        mask >>= 1
        bit += 1


def discover(prefer: str = "auto", gpu: int = 0) -> list[Candidate]:
    """Return scored candidates, best first. Does not pick -- caller decides."""
    cands: list[Candidate] = []

    if prefer in ("auto", "nvrm"):
        try:
            from .transport.nvrm import NvRmClient
            cli = NvRmClient(gpu_index=gpu)
            supported = cli.supported_display_mask()
            connected = cli.connected_display_mask(supported)
            for did in _display_ids(supported):
                tag = "conn" if (did & connected) else "disc"
                c = _probe(cli.bind(did), f"nvrm:display=0x{did:08x}[{tag}]", cli)
                if c:
                    cands.append(c)
        except Exception as e:
            if prefer == "nvrm":
                raise
            cands.append(_note(f"nvrm unavailable: {e}"))

    if prefer in ("auto", "drm"):
        try:
            from .transport.drm import DrmTransport, list_aux_devices
            for path in list_aux_devices():
                try:
                    c = _probe(DrmTransport(path), f"drm:{path}")
                    if c:
                        cands.append(c)
                except OSError:
                    continue
        except Exception:
            pass

    real = [c for c in cands if c is not None]
    real.sort(key=lambda c: c.score(), reverse=True)
    return real


def _note(msg: str):
    c = Candidate(None, msg, b"", 0, 0, False)
    return c


def find_hub(prefer: str = "auto", gpu: int = 0):
    """Return the best hub Candidate, or raise RuntimeError with diagnostics."""
    cands = discover(prefer, gpu)
    hubs = [c for c in cands if c.is_hub]
    if hubs:
        return hubs[0]
    detail = "; ".join(c.label for c in cands) or "no candidates"
    raise RuntimeError(f"no Synaptics MST hub found ({detail})")
