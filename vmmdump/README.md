# vmmdump — read a VMM5310 DP-MST hub from Linux over DP AUX

`vmmdump` reproduces, on Linux, what MegaChips/Synaptics **VMMTool.exe** captures
on Windows from the dock's **VMM5310** MST hub: chip/firmware identity, the
internal register space, a decoded view of the link / streams / DSC / outputs,
and the monitor EDIDs — all over the DisplayPort **AUX** channel.

It is the executable counterpart to [`../dump.txt`](../dump.txt) +
[`../VMM5310_dump_decoded.md`](../VMM5310_dump_decoded.md): the same data, read
live off the silicon instead of on Windows.

## The catch this tool solves: NVIDIA + DP AUX on Linux

The standard way to reach a Synaptics MST hub's DPCD on Linux is fwupd's
`synaptics-mst` plugin, which opens `/dev/drm_dp_aux*`. **The NVIDIA driver does
not create those nodes** (proprietary *and* `open-gpu-kernel-modules` — neither
calls `drm_dp_aux_register`). On this machine the dock's USB-C is wired only to
the NVIDIA dGPU, so that path is a dead end, and the I²C-over-AUX buses NVIDIA
*does* expose only reach DDC/EDID, not native DPCD (where the hub's RC registers
live at 0x4B0/0x500/0x507).

The way through: NVIDIA's kernel ABI exposes a native-AUX primitive as a
Resource-Manager control, **`NV0073_CTRL_CMD_DP_AUXCH_CTRL`** (a 20-bit DPCD
transaction), reachable from userspace via the `/dev/nvidiactl` `NV_ESC_RM_CONTROL`
ioctl. It is flagged `RMCTRL_FLAGS_PRIVILEGED` — i.e. **callable as root** — and
is the same control `nvidia-modeset` uses internally (`nvkms-rm.c: ReadDPCDReg`).
It works on the **stock proprietary driver**; no driver switch or reboot needed.

`vmmdump/transport/nvrm.py` implements that path. The tool is transport-agnostic:
a `drm` backend (`/dev/drm_dp_aux*`) is also provided for amdgpu/i915/nouveau hosts.

## Usage

Native AUX/RM access needs **root**.

```sh
sudo python3 -m vmmdump                     # detect hub, print identity + decode
sudo python3 -m vmmdump --edid              # + recover monitor EDIDs from SRAM
sudo python3 -m vmmdump --list-devices      # enumerate AUX sinks, show which is the hub

# full register dump matching VMMTool's address list, plus decoded + JSON:
sudo python3 -m vmmdump --addresses-from dump.txt \
     --raw vmm.out.txt --decode vmm.decoded.txt --json vmm.json --edid

# offline: decode an existing dump.txt with no hardware
python3 -m vmmdump --decode-file dump.txt --edid
```

Useful flags: `--transport {auto,nvrm,drm}`, `--gpu N` (NVIDIA index),
`--addresses-from dump.txt` (read VMMTool's exact ~2141-address list; without it
`--raw` dumps only the decoder's register set).

## How it works

```
transport/nvrm.py   NV0073 AUXCH over /dev/nvidiactl (alloc client->device->
                    subdevice->display-common, then RM_CONTROL); auto-chunks DPCD
transport/drm.py    /dev/drm_dp_aux* pread/pwrite (non-NVIDIA hosts)
detect.py           probe each sink for Synaptics OUI 90:CC + RC cap + chip 0x5xxx
rc.py               Synaptics Remote-Control: enable("PRIUS") -> ReadFromMemory -> disable
identity.py         OUI / chip id (0x507) / fw (0x50A) / family / board id
dumper.py           coalesce addresses into runs, RC-read, assemble little-endian u32
decode.py           link, RFRM stream timings (base+0x30 MSA), VESA DSC 1.2 PPS, TX
edid.py             scan the hub SRAM for valid 128-byte EDID blocks
report.py / cli.py  raw (re-parseable) + decoded text + JSON
```

RC reads are non-destructive: only `EnableRc` / `ReadFromMemory` / `DisableRc`
(plus the identity DPCD reads). The session is always wrapped enable→…→disable so
the hub never stays in remote-control mode.

## Verification (against the committed `dump.txt`)

Read live through the NVIDIA proprietary driver and diffed against the Windows
capture:

- **Identity** — `VMM5310`, Panamera, fw **5.04.135**, OUI `90:cc:24` — exact match.
- **Decoder registers** — every config/identity register the decoder interprets
  matches `dump.txt` (link rate HBR3, 2 lanes, MST 63 slots, RFRM0/RFRM2 MSA,
  full DSC 1.2 PPS, TX0 HDMI / TX2 DP 4-lane, fw build stamp).
- **Full raw dump** — ~92% of 2141 registers byte-identical; **100% of the
  differences are live-adaptive fields** (PHY equalizer taps, link-training
  status, FIFO/stream counters), proven dynamic by reading twice 1 s apart. These
  legitimately differ between two independent captures and are exactly the regions
  `VMM5310_dump_decoded.md` §13 flags as non-static.
- **EDIDs** — D1, ViewSonic VA3209-QHD, and the SYN3000 default all recovered.

Offline regression tests: `python3 vmmdump/tests/test_offline.py`.

## Credit / licensing

The Synaptics RC register map and opcodes were reimplemented from fwupd's
`plugins/synaptics-mst` (LGPL-2.1+, reference only). The NVIDIA RM ABI (structs,
ioctl codes, `NV0073` control) is from NVIDIA's `open-gpu-kernel-modules` SDK
headers (MIT); the client handshake mirrors tinygrad's `ops_nv.py`. This tool is
WTFPL like the rest of the repo. No warranty — it talks to a live display link.
