# vlidump — read the dock's VIA Labs USB hub (the USB half)

`vlidump` is the USB-side companion to [`../vmmdump`](../vmmdump). Where `vmmdump`
reads the **MegaChips VMM5310** DP-MST hub over DP AUX, `vlidump` reads the
**VIA Labs hub** that carries USB 3 over the *other* lane pair of the same Type-C
cable. Same teardown, the other half of the silicon.

## The two halves of the dock

This USB-C dock (HyperDrive GEN2 18-port, **HD-G218**) splits its 4 Type-C
high-speed lanes:

- **2 lanes → VMM5310** (DisplayPort, HBR3, DSC) — the original teardown.
- **1 SuperSpeed pair → VIA Labs hub** (USB 3.2 Gen2, 10 Gbps).

The USB side is two cascaded VLI hubs plus a billboard:

| USB id | chip | role |
|---|---|---|
| `2109:0822` | **VL822** | USB 3.2 Gen2 (10G) hub — upstream, on the Type-C SS pair |
| `2109:0817` | **VL817** | USB 3.1 Gen1 (5G) hub — cascaded downstream |
| `2109:8818` | (VL822 billboard) | USB Billboard device advertising DP Alt Mode |

## Why this is easy where the DP side was hard

The DP/AUX side needed NVIDIA's RM ioctl because the driver exposes no
`/dev/drm_dp_aux*`. The USB side has no such problem: the VL822 **enumerates as
an ordinary USB device**, so we reach it straight through usbfs
(`/dev/bus/usb/BBB/DDD` + `USBDEVFS_CONTROL`). stdlib only — `ctypes`/`fcntl`,
no pyusb, matching `vmmdump`'s no-dependencies rule.

VLI hubs expose their registers and external SPI flash via vendor control
transfers on the default endpoint. The request encoding was reimplemented from
fwupd's `plugins/vli` (LGPL-2.1+, **reference only — not copied**), the same
clean-room approach `vmmdump/rc.py` took for synaptics-mst:

```
register read (1B)  control-IN  bRequest=addr>>8  wValue=addr&0xff  wIndex=0
SPI flash read      control-IN  bRequest=0xC4     wValue=(addr hi)|opcode  wIndex=swap16(addr)
```

Everything here is **read-only** — `usbfs.py` exposes only `control_in`, only
SPI read opcodes are used (READ_DATA 0x03 / RDID 0x9F), and it never claims or
detaches the kernel `hub` driver, so dumping does not disturb the storage and
network devices sitting behind the hub.

## Usage

Needs **root** (usbfs control transfers).

```sh
sudo python3 -m vlidump                       # identify chip + flash JEDEC id
sudo python3 -m vlidump --regs f88c,f88e,f88f # read arbitrary registers
sudo python3 -m vlidump --spi-dump fw.bin --spi-len 0x80000   # dump 512K flash
```

## What it reads live (this dock)

```
=== VIA Labs USB hub 2109:0822 ===
fw (bcdDevice)  : 6.43                 # OEM-customized build
chip id         : 18 35  (alt 77 6d)   # VL822 silicon signature
flash JEDEC id  : a1 40 13             # Fudan FM25F04, 4Mbit / 512KB SPI-NOR
```

Flash offset 0 holds a small VLI firmware header (`05 18 30 00 20 00 84 80 …`);
the rest of the 512 KB is firmware blocks and erased (`0xFF`) gaps.

## Firmware / update status

- **fwupd's `vli` plugin does not claim this device.** Even as root,
  `fwupdtool get-devices --plugins=vli` returns *No detected devices*: fwupd's
  VLI coverage is keyed to OEM-rebadged VID/PIDs (Lenovo `17EF`, etc.), not the
  stock VIA Labs `2109` a whitelabel dock uses.
- **Latest public VL822 firmware** is VIA Labs' reference **VL822-Q8 v5553
  (2022-07-25)**, distributed only as a Windows tool. It is *not* a drop-in for
  this dock — the dock runs an OEM build (`6.43`) with Hyper's descriptors and
  port map; flashing reference firmware would overwrite those.
- Writing/updating firmware is therefore **out of scope** for this tool. The DP
  side's updater (`VmmUpdater.exe`, Windows) and the VLI side's tool are both
  vendor Windows binaries; reproducing a *writer* would mean leaving the
  non-destructive boundary this repo holds to. `vlidump` reads only.
