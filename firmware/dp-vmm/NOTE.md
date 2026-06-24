# DP side (VMM5310) — firmware status

No flashable VMM5310 / VMM53xx firmware image is publicly available from any
source: Synaptics/Kinetic do not publish it, Hyper's support site (which once
hosted the per-model `VmmUpdater.exe` + `.eeprom`) is closed and was not archived,
and the public Synaptics `VmmUpdater` packages (Dell/HP) are for *different* chips
(VMM2320/VMM3320) and bot-walled.

What is here instead:

- `vmm5310_live.regs.txt` / `.decoded.txt` / `.json` — a live read of *this* dock's
  VMM5310 over DP-AUX via `vmmdump` (identity + 2141 registers + decode + EDIDs).
  This is the chip's runtime state, not a flash image; it complements the repo's
  ground-truth `../../dump.txt` (note TX1 is an active DP output here, idle there).

The chip's actual firmware lives in an external SPI flash readable via the RC
`ReadFromEeprom` path (`vmmdump.rc.SynapticsRC.read_eeprom`), but on Panamera that
returns all-zero while the on-chip ESM (MCU) is running. Pulling the real image
needs an ESM-disable + reset write sequence that blanks the live display link —
deliberately not implemented (this repo stays read-only/non-destructive).
