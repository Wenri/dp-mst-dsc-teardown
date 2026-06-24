# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A hardware reverse-engineering teardown of the Kinetic/MegaChips **VMM5310** DP MST hub inside a USB-C dock. It has two halves:

1. **Data + analysis** (the original teardown):
   - `dump.txt` — ground-truth register/EDID dump captured on Windows with MegaChips' VMMTool. **Immutable**: the offline tests assert exact values against it (2141 registers). Never regenerate or edit it.
   - `VMM5310_dump_decoded.md` — the annotated decode. It draws an explicit line between *proven from the bits* (standards-defined: DP link, MSA timings, VESA DSC 1.2 PPS) and *inferred by correlation* (chip-proprietary registers, §13). Preserve that distinction when extending the analysis.
   - `reference/` — third-party PDFs (datasheets, dock manual). **Not WTFPL** — see `NOTICE`.

2. **`vmmdump/`** — a stdlib-only Python package that reproduces the VMMTool dump live on Linux over the DP AUX channel. No packaging config; it runs as a plain directory from the repo root.

3. **`vlidump/`** — the USB-side counterpart: a stdlib-only package that reads the dock's **VIA Labs VL822/VL817** USB hub over plain usbfs (the VL822 enumerates as a normal USB device, so no NVIDIA RM ioctl is needed). Read-only; protocol reimplemented from fwupd's `vli` plugin (LGPL — reference only, never copy). Also decodes VL8xx flash images offline: `vlidump/i8051.py` is a compact stdlib MCS-51 disassembler and `vlidump/fw.py` decodes the container + USB descriptors + 8051 entry (`python3 -m vlidump --decode-fw <image>`). Findings in `USB_side_decoded.md`. Live read: `sudo python3 -m vlidump`.

## Commands

```sh
# offline regression tests (decode the committed dump.txt, no hardware)
python3 vmmdump/tests/test_offline.py
# also pytest-compatible; single test:
pytest vmmdump/tests/test_offline.py::test_register_count

# offline decode of an existing dump (no hardware, no root)
python3 -m vmmdump --decode-file dump.txt --edid

# live against the hub (needs root; NVIDIA RM or /dev/drm_dp_aux*)
sudo python3 -m vmmdump                     # detect + identity + decoded summary
sudo python3 -m vmmdump --list-devices      # enumerate AUX sinks
sudo python3 -m vmmdump --addresses-from dump.txt \
     --raw vmm.out.txt --decode vmm.decoded.txt --json vmm.json --edid
```

There is no build/lint setup and no third-party dependencies (NVIDIA ioctls are done with `ctypes`/`fcntl` directly). Keep it stdlib-only.

## Architecture of `vmmdump`

Layered pipeline; each layer only knows the one below:

- **`transport/`** — `AuxTransport` protocol (`base.py`): native DPCD reads/writes at 20-bit addresses, auto-chunked to 16-byte AUX transactions. Two backends:
  - `nvrm.py` — NVIDIA RM ioctls on `/dev/nvidiactl` (`NV0073_CTRL_CMD_DP_AUXCH_CTRL`, root-only). Exists because the NVIDIA driver never creates `/dev/drm_dp_aux*` nodes. ABI structs come from NVIDIA's open-gpu-kernel-modules SDK headers.
  - `drm.py` — `/dev/drm_dp_aux*` pread/pwrite for amdgpu/i915/nouveau hosts.
- **`rc.py`** — Synaptics "Remote Control" protocol layered on DPCD `0x4B0–0x4CF`: `enable("PRIUS")` → command/poll/result → `disable`. Register map reimplemented from fwupd's `synaptics-mst` plugin (LGPL — **reference only, never copy its code**).
- **`detect.py`** — probes every sink on every transport for Synaptics OUI `90:CC` + RC capability + chip id `0x5xxx`; scores candidates.
- **`addresses.py`** — parser for the VMMTool `dump.txt` format (register section, memory section, EDIDs). Dual use: derives the exact address list for live re-dumps, and provides the register map for offline decoding.
- **`identity.py` / `dumper.py` / `edid.py`** — read identity DPCD + board id, coalesce addresses into runs and RC-read them, scan hub SRAM for valid 128-byte EDID blocks.
- **`decode.py`** — interprets registers into link / RFRM stream timings (MSA at base+0x30, packed `(vertical<<16)|horizontal`) / DSC 1.2 PPS / TX outputs. Register meanings trace back to `VMM5310_dump_decoded.md`.
- **`report.py` + `cli.py`** — text/raw/JSON output. The `--raw` format is intentionally re-parseable by `addresses.py` (same shape as `dump.txt`).

The CLI has three modes: `--list-devices`, offline (`--decode-file`, transport/RC never touched), and live (default).

## Hard constraints

- **This tool talks to a live display link.** RC sessions must always be wrapped enable → … → disable (use the `finally` in `cli.py` or `SynapticsRC` as a context manager), and only non-destructive **read** opcodes are allowed: ENABLE/DISABLE_RC, ReadFromMemory, ReadFromTxDpcd, ReadFromEeprom. Never issue flash/EEPROM-**write** opcodes. Note: on Panamera, `ReadFromEeprom` returns all-zero while the on-chip ESM (firmware MCU) is running — getting real flash bytes needs an ESM-disable + reset write sequence that blanks the live link, which this repo deliberately does NOT implement.
- Live-adaptive registers (PHY taps, training status, counters) legitimately differ between captures — only ~92% of a full dump is byte-stable vs `dump.txt`. Don't treat such diffs as bugs; §13 of the decode flags these regions.
- Generated outputs (`vmm_*.out.txt`, `vmm_*.decoded.txt`, `vmm_*.json`) are gitignored — keep that naming for new outputs.
- License is WTFPL; new source files carry `# SPDX-License-Identifier: WTFPL`. Don't add code derived from LGPL fwupd sources (reimplement) and don't claim ownership of anything under `reference/`.
