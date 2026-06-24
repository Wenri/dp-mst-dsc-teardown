# The USB half of the dock — VIA Labs VL822/VL817 (+ PD)

Companion to [`VMM5310_dump_decoded.md`](VMM5310_dump_decoded.md). That document
decodes the **DisplayPort** half of the HD-G218 dock (the MegaChips/Kinetic
VMM5310 MST hub). This one decodes the **USB** half, read live with
[`vlidump/`](vlidump/) over plain USB.

Same convention as the DP decode: an explicit line between **proven** (standards
fields, USB descriptors, JEDEC id, register reads off the live chip) and
**inferred** (chip-internal meanings without VIA Labs' NDA register manual).

## Why the dock needs a second chip at all

A USB-C connector carries four high-speed lanes. In DP Alt Mode the dock split
them **2 + 2**: two lanes to the VMM5310 for DisplayPort, the remaining
SuperSpeed pair to a **VIA Labs USB 3 hub**. That single decision is why the DP
trunk is only 2-lane HBR3 (≈12.96 Gb/s) and therefore why DSC is *load-bearing*
on the DP side — half the connector was reserved for USB. The USB half is that
reservation, made silicon.

## The chips (proven)

USB enumeration + live register reads identify three functions:

| USB id | chip | role | evidence |
|---|---|---|---|
| `2109:0822` | **VL822** | USB 3.2 Gen2 (10 Gbps) hub, upstream on the Type-C SS pair | `bcdUSB 3.20`, enumerates at 10000M, sysfs `2-1` |
| `2109:0817` | **VL817** | USB 3.1 Gen1 (5 Gbps) hub, cascaded under the VL822 | `bcdUSB 3.10`, 5000M, sysfs `2-1.1` |
| `2109:8818` | (VL822 billboard) | USB Billboard device advertising DP Alt Mode | `bDeviceClass 17 Billboard` |

Topology: host xHCI → `2-1` VL822 (10G) → `2-1.1` VL817 (5G) → Gigabit Ethernet
(RTL8153). Each USB-3 hub presents a SuperSpeed half (`08xx`) and a USB-2 half
(`28xx`) sharing one firmware revision, so each is one chip.

## How it was read (proven, method)

Unlike the DP/AUX side — which needs NVIDIA's RM ioctl because the driver creates
no `/dev/drm_dp_aux*` — the VL822 enumerates as an ordinary USB device, so
`vlidump` reaches it straight through **usbfs** (`USBDEVFS_CONTROL`, stdlib
ctypes/fcntl). The VLI vendor protocol was reimplemented from fwupd's `plugins/vli`
(LGPL, reference only — not copied):

```
register read (1B)  vendor control-IN  bRequest=addr>>8  wValue=addr&0xff  wIndex=0
SPI flash read      vendor control-IN  bRequest=0xC4     wValue=(addr.hi)|opcode  wIndex=byteswap16(addr)
```

Read-only by construction: only control-IN transfers, only SPI read opcodes
(READ_DATA 0x03 / RDID 0x9F), and the kernel `hub` driver is never detached, so
reading does not disturb the storage/network behind the hub.

## Live readout (proven)

```
fw (bcdDevice)  : 6.43            # OEM-customized build (running)
chip id regs    : 18 35  (alt 77 6d)   # VL822 internal id (raw)
chip ver / pkg  : 0xf0 / 0x00 ; 0xe5
flash JEDEC id  : a1 40 13        # Fudan FM25F04 — 4Mbit / 512KB SPI-NOR
```

`A1` = Fudan Microelectronics; capacity byte `0x13` = 4 Mbit = 512 KB.

## Flash layout (proven — 512 KB read in full)

Only ~12% of the flash is used; the rest is erased (`0xFF`).

| Region | Size | Contents |
|---|---|---|
| `0x00000` | 256 B | section header (`05 18 30 00 20 00 84 80 …`) |
| `0x02000–0x0a500` | ~34 KB | **VL822 hub firmware** (8051-class code) + USB descriptors |
| `0x20000–0x27600` | ~30 KB | **USB-C PD 3.0 controller** firmware + descriptors |
| `0x27f00` | 256 B | trailer/config |
| rest | ~360 KB | erased |

### Device descriptor @ `0x07bf2` (proven decode)

`12 01 20 03 09 00 03 09 09 21 22 08 40 06 01 02 03 01` →
bLength 18, DEVICE, **bcdUSB 0x0320**, class 09 (Hub), bMaxPacket0 512,
**idVendor 0x2109, idProduct 0x0822**, **bcdDevice 0x0640**, iMfr/iProd/iSerial 1/2/3.

Note the **flash says bcdDevice 6.40 but the live device reports 6.43** — the
running firmware bumps the version (or an in-field patch did); flagged, not
explained.

### String descriptors (UTF-16LE, proven)

- Hub bank: `"VIA Labs, Inc."`, `"USB3.1 Hub"`, `"USB2.0 Hub"`, `"USB-C Device"` (billboard).
- PD bank: `"VLI Inc."`, **`"USB-C PD3.0 Device"`**, and `http://help.vesa.org/dp-usb-type-c/`
  (the VESA Billboard "additional info" URL).

The PD bank settles a question the DP-side teardown left open: the dock's USB-C
**PD/alt-mode controller is also VIA Labs**, and its firmware shares this same
flash. The full USB-C front end (hub + PD3.0 + billboard) is VLI.

## Reference firmware (proven to exist; not committed)

VIA distributes reference firmware via station-drivers (no login). The dock's
**VL817 reports `bcdDevice 03c4`, and VIA's reference build `03C4` exists**
(2020-05-11); the primary hub's reference is **VL822-Q8 v5553** (2022-07-25). These
ship with VIA's Windows MP/ISP tooling (`HUBIspTool.exe`, `HubUpgradeFW.exe`).
They are OEM-generic, not drop-in for this dock's `6.43` build, and are
copyrighted — archived under [`firmware/`](firmware/) with attribution in
[`NOTICE`](NOTICE) (the WTFPL does not cover them).

## Decoding the firmware

`vlidump --decode-fw <image>` decodes a VL8xx flash image offline, in three
layers (no 8051 disassembler ships on a typical box and capstone has no 8051
core, so `vlidump/i8051.py` is a compact stdlib MCS-51 disassembler — it decodes
the hub bank with 0% unknown opcodes). Decoded from our live `6.43`:

**Container (proven magic; header fields inferred).** `magic 05 18` = VL822
(`05 38` = VL817), `code@0x2000` (header byte pair `20 00` = page count ×256).
Section map: header `0x0`, hub bank `0x02000–0x0a500`, VL103 PD bank
`0x20000–0x27600`, trailer `0x27f00`. Header `type 0x0030`, `word6 0x8084`, tail
`02 a2` are not yet placed (likely size/checksum).

**USB descriptors (proven, USB spec).** Four device descriptors, decoded straight
from flash — the dock's complete USB-visible identity set:

| @flash | descriptor | class |
|---|---|---|
| `0x07bf2` | `2109:0822` USB 3.20, bcdDevice **6.40** | 09 Hub (VL822 SS) |
| `0x07d1b` | `2109:2822` USB 2.10 | 09 Hub (USB-2 companion) |
| `0x08440` | `2109:8818` USB 2.01 | 11 Billboard (hub alt-mode) |
| `0x24e35` | `2109:0103` USB 2.01 | 11 Billboard (**VL103 PD**) |

(Flash says bcdDevice **6.40**; the live device reports **6.43** — the running
firmware bumps it.) All 11 string descriptors decode too (`"VIA Labs, Inc."`,
`"USB3.1 Hub"`, `"USB-C Device"`, `"VLI Inc."`, `"USB-C PD3.0 Device"`).

**8051 code (opcodes proven; semantics not).** Reset `LJMP 0x7286`; interrupt
vectors at 0x03/0x0B/0x13/0x1B (INT0/Timer0/INT1/Timer1). The reset handler is a
recognizable C-runtime start:

```
7286: MOV R0,#0x7F / CLR A / MOV @R0,A / DJNZ R0,0x7289   ; zero IRAM 0x00-0x7F
728C: MOV SP,#0xA4                                         ; set stack
728F: LJMP 0x72CD                                          ; -> main init
```

The full decode is committed at
[`firmware/usb-vli/vl822_live_fw_6.43.decoded.txt`](firmware/usb-vli/vl822_live_fw_6.43.decoded.txt).

## Comparing all five VL822 builds

`vlidump/trace.py` does recursive-descent "following" from the reset + interrupt
vectors (handling LJMP/AJMP/SJMP, conditional branches, L/ACALL → functions,
stopping at RET, flagging `JMP @A+DPTR` jump tables). `vlidump --follow <image>`
prints the per-image summary. Because the builds aren't address-aligned, code is
compared by **opcode-only function fingerprints** (operands/addresses dropped), so
the same routine matches across builds despite relocation. Full report:
[`firmware/usb-vli/vl822_builds_compared.txt`](firmware/usb-vli/vl822_builds_compared.txt).

What the five builds (our `6.43` + the four reference bins) show:

- **Same codebase + toolchain.** Each is ~14–15 KB reachable code, ~240–260
  functions, 2–4 jump tables — and the **crt0/reset handler is opcode-identical
  across all five** (only relocated). They're one firmware family built the same way.
- **50–72% of functions are shared** (fingerprint jaccard). The references cluster
  tightest (5554↔0823 72%, 5554↔9043 68%); our **`6.43` is closest to `5553`**
  (60.5%, **164 shared functions**, 53 live-only, 54 ref-only) — matching the
  byte-level hint from the earlier comparison.
- **The differences are layered, not random:**
  - *branding* — `5553/5554` carry Lenovo descriptors (`PID30Dx`, so 0 `2109`
    descriptors found); `9043`/`0823` keep `2109`.
  - *variant* — `Q5 0823` exposes `2109:1822`/`4822` (different port map) vs the
    `Q7/Q8`'s `0822`.
  - *tier* — header `type` is `0x0040` only for `5554` (Tier2); the rest `0x0030`.
  - *our dock is the outlier in scope* — only the `6.43` image bundles the
    **billboard (`8818`) and the VL103 PD bank (`0103`)**; every reference bin is
    hub-only (~32–40 KB, no PD, no alt-mode billboard).

So our `6.43` is a legitimate VL822 build from the same source tree as VIA's
references, closest to the 5553 mainline, customized for this dock (descriptors +
the bundled VL103 PD/billboard) — not a fork and not a copy of any single reference.

## Proven vs inferred

- **Proven:** the chip identities (USB descriptors + PIDs), the topology, the
  JEDEC flash part, the full 512 KB flash map, the device/string descriptors, the
  PD3.0 bank, the lane-budget rationale, the read method, the 8051 container
  layout + vector table + reset handler (disassembled).
- **Inferred / not yet placed:** the header size/checksum words, the meaning of
  the VL822 internal id/ver/package registers (`0xf88c/0xf88e/0xf651…`), and the
  semantics of the 8051 routines beyond the crt0 entry. These need VIA Labs' NDA
  register manual / a deeper disassembly pass.
