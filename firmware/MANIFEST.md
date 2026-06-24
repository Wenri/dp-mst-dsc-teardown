# Firmware collection — dock teardown (DP = VMM5310, USB = VL822/VL817)

Third-party copyrighted firmware/tools, collected for the teardown and committed
here by choice. **The WTFPL does not cover them** — attribution and scope are in
[`../NOTICE`](../NOTICE) (same discipline as the `reference/` PDFs). Full hashes in
`hashes.txt`.

## USB side — VIA Labs (COMPLETE)

### Live readout — our actual chip
| File | What | Notes |
|---|---|---|
| `live/vl822_live_fw_6.43.bin` | VL822's entire 512 KB SPI flash, read live via `vlidump` | OEM build **6.43**; hub bank + USB-C PD3.0 bank + descriptors; rest erased |

### Reference firmware + MP/update tools (station-drivers, no login)
**VL822 bundle** — `VL822_firmwares.zip` v5553, 2022-07-25 (id 5431/5430, the Q7/Q8 downloads are byte-identical):
- `Binfile/VL822_Q8_5553_PID30Dx_Hybrid_Tier1_20211019.bin` — reference for our **primary** hub (VL822)
- `Binfile/VL822_Q7_5554_PID30Dx_Hybrid_Tier2_20211019.bin`
- `Binfile/VL822_Q7_9043_Phantom_20220616.bin`
- `Binfile/VL822_Q5_0823_Phantom_20220616.bin`
- `HUBIspTool.exe` — **ISP / mass-production programmer** (the "MP tool")
- `HubUpgradeFW.exe` — field firmware updater · `VLIHubAPI.dll` — the API it calls
- `HubFilterDriver{Add,Remove}.exe`, `DriverFilter/` (devcon) — driver-detach helpers for flashing

**VL817 bundle** — v03C4, 2020-05-11 (id 4101):
- `VL817/VL817_P2BC_Apple1A_Q7_03C4_..._20180613.bin` — **matches our secondary hub** (`bcdDevice 03c4`)
- `VL817/VL817_LowPower_U1U2_Q7_03C3_ConnectedWithVL10X_...bin` — dock variant paired with a VL10x PD controller
- `VL102/VL102_R87_..._Anker_A8321.bin`, `VL102/VL103_App5-...bin` — **VL102/VL103 USB-C PD controller** firmware
- `VLIHubPDUpdateTool_V1.0.1.9_..._20181120.exe` (+ `.7z`) — hub + PD field updater

## DP side — MegaChips/Kinetic VMM5310 (NOT publicly available)

No flashable VMM5310 / VMM53xx firmware exists from any public source:
- Synaptics does not publish VMM5310 firmware; the dock's OEM build (**5.04.135**) lived only in Hyper's now-closed help center (not archived in Wayback — only screenshots survived).
- Public Synaptics `VmmUpdater.exe` packages (Dell/HP) are for **different chips** (VMM2320/VMM3320), and Dell/HP block automated download (HTTP 403).

What we do have / can get for the DP side:
- `../../dump.txt` (in repo) — the VMM5310's ground-truth register + EDID state.
- **Best path to the real image:** dump the VMM5310's SPI EEPROM live via the Synaptics RC `ReadFromMemory`/`ReadFromEeprom` path — the DP-AUX equivalent of how `vlidump` dumped the VL822 over USB. Read-only, but `ReadFromEeprom` is outside `vmmdump`'s default sanctioned opcode set, so it needs an explicit go-ahead.

## Sources
- station-drivers.com — VIA VL822 fw v5553 (id 5431), VL817 fw v03C4 (id 4101)
- VL822 firmware page: oemdrivers.com/labs-vl822
- DP-side tool family (different chips): Dell "Synaptics VMM2320/VMM3320 MST HUB Firmware"
- fwupd issue #1665 — extracting `.eeprom` from the official Windows VMM updater
