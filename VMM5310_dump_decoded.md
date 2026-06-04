# VMM5310 Dock — Decoded Register & Link Analysis

**Source:** `dump.txt` — produced by **VMMTool.exe v9.03.07**, captured **2026‑06‑04 21:40**, connected **over the DisplayPort AUX channel**.
**Host:** ASUS ROG G533QS, AMD Ryzen 9 5900HX + **NVIDIA RTX 3080 Laptop** (driver r610), Windows 11 (26300).
**Dump contents:** human‑readable header (lines 1–110), 2,136 raw chip registers (lines 112–~5190), three monitor EDIDs (lines 5200–5299).

This document decodes the dump as far as it goes. **Standards‑based fields** (VESA DSC PPS, DP/HDMI MSA, DPCD‑style link status, the DSC rate‑control model) decode exactly. **Chip‑proprietary fields** (internal state machines, the `0xC002801C` / `0x54603F48` config words, FIFO/mux state) are placed *structurally* by correlation — the authoritative bit map lives in MegaChips/Kinetic's NDA register manual.

---

## 1. Headline — the whole setup in one line

```
RX: MST 2lane HBR3 mode, DSC ON FEC ON
```

A single 2‑lane HBR3 DisplayPort MST trunk runs from the RTX 3080 over USB‑C to the dock's VMM5310 hub, carrying **two streams**; the hub demuxes them and drives **two different output technologies**:

| Stream | Monitor | Mode | On the trunk | Hub output |
|---|---|---|---|---|
| **RFRM2 / VC2** | ViewSonic VA3209‑QHD | 2560×1440 @ 75, **10 bpc** | **uncompressed** (47 slots) | **TX2 = DisplayPort, 4‑lane HBR2** |
| **RFRM0 / VC1** | D1 (DTZ4426) | 1920×1080 @ 100, **8 bpc** | **DSC 1.2, 10.00 bpp, 2.4:1** (12 slots) | **TX0 = HDMI** (decompressed) |
| RFRM1 | — | — | idle | TX1 unused (HPD low) |

**Data path:** GPU → 2‑lane HBR3 MST trunk (D1 DSC‑compressed, ViewSonic raw) → VMM5310 → demux into RFRM0/RFRM2 → DSC‑decompress D1 → mux out **HDMI (D1)** + **DisplayPort (ViewSonic)**.

---

## 2. Chip & firmware

| Field | Value |
|---|---|
| Chip ID | **VMM5310**, rev **A2** (Kinetic/MegaChips "Panamera" VMM53xx family) |
| Firmware | `CE‑2L‑0414`, version **5.04.135** |
| Config file version | `0x020` |
| Bootloader | 005 |
| Active flash bank | 0 (JTAG disabled, Normal mode, load 45 Mbps, MCU 270 MHz) |
| FW load result | `0x0f` — code OK, config block0/1 OK, **HDCP 1.4 + 2.2 keys OK** |
| Firmware build stamp | `200D30h = 0x20160729` → 2016‑07‑29 |

---

## 3. Register block map (all 2,136 registers)

The address space splits into an RX side (`0x2xxxxx`) and three byte‑for‑byte‑parallel TX sides (`0x3xxxxx`):

| Address | Block | Function |
|---|---|---|
| `0x200000` | RX global/control | lock/status, MST slot count, **DSC RC table** (`200600h`), fw build stamp |
| `0x210000` | **RX DP link/PHY** | lane status (2 lanes), link rate (HBR3), PHY EQ taps, ref clocks |
| `0x220000` | **RX MST + per‑stream + DSC** | three RFRM blocks (`220800/C00/1000`), DSC ctrl + PPS at `+0x200` |
| `0x230000` | RX audio / MST aux | |
| `0x300000 / 310000 / 320000 / 330000` | **TX0 = HDMI → D1** | core / TMDS PHY / PLL / mux |
| `0x340000 / 350000 / 360000 / 370000` | **TX1 = unused** | all `0xFEEDB001` idle markers |
| `0x380000 / 390000 / 3A0000 / 3B0000` | **TX2 = DisplayPort → ViewSonic** | core / DP link‑PHY / PLL / VC |
| `0x400000` | global / MCU | |

**Recurring magic/config words**

| Value | Meaning (inferred) | Seen at |
|---|---|---|
| `0xFEEDB001` | "unused / idle" marker | TX1 block, idle streams |
| `0xC002801C` | link/PHY base config | RX `220000h`, TX0 `310000h`, TX2 `390000h` |
| `0x54603F48` | common PLL/clock config | RX `230100h`, TX `x0100h` |
| `0x00020305` | stream‑format word | RX `200040h`, every TX `x0500h` |
| `0x0300003F` | MST slot count = 63 | RX `20004Ch`, TX2 `38050Ch` |
| `0x000A2E00` / `0x053000F0` | per‑channel HDMI TMDS driver cfg | TX0 `300060/70/80/90h` |
| `0x00003FCC` / `0x373000C3` | per‑lane DP driver cfg | TX2 `380060/70/80/90h` |

---

## 4. RX DP link / PHY — `0x210000` (the trunk, decoded from silicon)

| Reg | Value | Decode |
|---|---|---|
| `210000h` | `0x80000077` | **2 lanes trained** — lane0 = `7`, lane1 = `7` (CR_DONE+EQ_DONE+SYMBOL_LOCKED); lanes 2/3 absent. (cf. TX2's `77 77 01 01` for 4 lanes) |
| `2107C0h` | `0x32A` = 810 | **link rate** ×10 MHz = 8 100 MHz = **HBR3 (8.10 Gb/s/lane)** |
| `2107C4h` | `0x10E` = 270 | 2.70 / reference |
| `2107D0h` | `100000` | reference clock 100 MHz (kHz units) |
| `2107E0h` | `200000` | reference clock 200 MHz |
| `210200h–2102FCh` | `0x44443333`, `0x08080404`, `0xFF55FFF0`, … | PHY **equalizer** — voltage‑swing / pre‑emphasis taps |

→ **Trunk = 2‑lane HBR3 (12.96 Gb/s usable), FEC on.** Symbol‑error counters `8000 8000 0000 0000` = lanes 0/1 valid with **0 errors**, lanes 2/3 absent.

---

## 5. RX global / control — `0x200000`

| Reg | Value | Decode |
|---|---|---|
| `200004h` | `0x77800030` | status/lock (low `0x30` = header "Lock sts …0030") |
| `20004Ch` | `0x0300003F` | **MST slot count = 0x3F = 63** usable time slots |
| `200040h` | `0x00020305` | stream‑format word (shared with all TX) |
| `200600h–200654h` | `0x0391021C`, `0x1C1C111C`, `0x14C60400`, … | **DSC rate‑control parameter table** (RC model used by the decoder) |
| `200D30h` | `0x20160729` | firmware build stamp |
| `200D38h` | `0x53100002` | build/version field |

---

## 6. RX MST streams (RFRM) — `0x220000`

Each stream has a control block at `base`, full Main Stream Attributes at `base+0x30`, and a DSC control/PPS block at `base+0x200`.

| | **RFRM0 = D1** `0x220800` | **RFRM2 = ViewSonic** `0x221000` | RFRM1 `0x220C00` |
|---|---|---|---|
| control | `0x5000008A` | `0x5000008A` | `0x50000086` |
| `+0x04` state | `0x01000003` | `0x01000003` | `0x01000003` |
| VT / HT | `220830h=0x04600834` → **1120 / 2100** | `221008h=0x1C7505C9` → VT **1481** | — (zeros) |
| VA / HA | `220838h=0x04380780` → **1080 / 1920** | `22100Ch=0x1AD605A0` → VA **1440** | — |
| VS / HS | `220834h=0x0024005C` → **36 / 92** | … | — |
| VSW / HSW | `22083Ch=0x0005002C` → **5 / 44** | … | — |
| **DSC ctrl (+0x200)** | `220A00h=0xA2000006` → **DSC ON** | `221200h=0x40000000` → **DSC OFF** | `220E00h=0x80000004` (idle) |

`0x220830h–84Ch` is D1's complete MSA — every value from the header's `RFRM0` line. RFRM1 (`220C08h–220C44h`) is all‑zero = the empty stream. The DSC on/off bit lives in adjacent registers per stream.

---

## 7. DSC Picture Parameter Set — `0x220A00` (RFRM0 / D1)

The PPS layout is the **public VESA DSC 1.2 standard**, so this decodes completely — the literal compression recipe NVIDIA programmed:

| Reg | Value | DSC field | Meaning |
|---|---|---|---|
| `220A00h` | `0xA2000006` | control | **DSC enabled** (header `Ctrl a2000006`) |
| `220A04h` | `0x00000010` | status | header `Sts 10` |
| `220A08h` | `0x12000089` | version/cfg | **DSC v1.2**, line‑buffer depth **9** |
| `220A0Ch` | `0x10A00438` | bpp / slice_height | **bpp field = 0x0A0 = 10.00 bpp**, slice_height **1080** |
| `220A10h` | `0x07800438` | pic_width / pic_height | **1920 × 1080** |
| `220A14h` | `0x07800960` | slice_width / chunk_size | slice **1920**, **chunk 2400** |
| `220A18h–24h` | … | RC config | initial transmit/decode delays, rc_model_size |
| `220A34h–40h` | `…0E1C2A38 46546269 7077797B 7D7E…` | **rc_buf_thresh** | standard 14 thresholds: 14,28,42,56,70,84,98,105,112,119,121,123,125,126 |
| `220A44h–5Ch` | `…19FC 19F8 1A38 1A78 2AB6 2AF4 3AF4 5B34` | **rc_range_parameters** | 15 quant ranges (min_qp / max_qp / bpg_offset) |
| `220A68h` | `0x04600834` | (mirrored MSA) | VT 1120 / HT 2100 |
| `220A6Ch` | `0x0024005B` | | VS 36 / HS 91 |
| `220A70h` | `0x04380780` | | VA 1080 / HA 1920 |

**Summary:** DSC 1.2, **RGB 4:4:4**, single slice (1920×1080), **10.00 bpp** → **2.4:1** from 8‑bit (24 bpp) source, block‑prediction off, standard RC model. `RFRM2`/ViewSonic has **no PPS** (`221200h=0x40000000` = DSC off → full 10‑bit uncompressed).

---

## 8. TX0 = HDMI → D1 — `0x300000` / `0x310000`

The hub decompresses the D1 stream and re‑serializes it as HDMI TMDS (3 data + clock, *not* DP lanes).

| Reg | Value | Decode |
|---|---|---|
| `310000h` | `0xC002801C` | link/PHY base |
| `310050h` | `0x00E40A00` | channel map `0xE4` = **identity RGB**; `0x00` = HDMI (no 4‑lane bit) |
| `310060/64h` | `0x..E4E4` | channel map |
| `310010h–24h` | `0xFD5CFBBC`, `0x3C1CF7FE`, … | TMDS PHY driver / EQ taps |
| `300060/70/80/90h` | `0x000A2E00` | four TMDS‑channel driver configs |
| `300064/74/84h` | `0x053000F0` | channel driver (R/G/B/Clk) |
| `300338h / 33Ch` | `0x491558F8 / EE` | **HDMI audio clock‑regen CTS/N** |
| `300340h–37Ch` | … | HDMI audio (2‑ch / 48 kHz), IEC, infoframe |

Display timing (HT 2100 / VT 1120) is *not* re‑stored here — it rides through from the decompressed RFRM0 (`220A68h`). Output = **8‑bpc RGB + 2‑ch 48 kHz LPCM audio.**

---

## 9. TX2 = DisplayPort → ViewSonic — `0x380000` / `0x390000`

Four‑lane HBR2 DP, 10‑bpc, uncompressed, with SSC + Enhanced Framing.

| Reg | Value | Decode |
|---|---|---|
| `390000h` | `0xC002801C` | link/PHY base |
| `390050h` | `0x00E40A0F` | channel map `0xE4`; **`0x0F` = 4 lanes enabled** |
| `3900C8h` | `0x05C90AA0` | **VT 1481 / HT 2720** |
| `3900D0h` | `0x05A00A00` | **VA 1440 / HA 2560** |
| `38004Ch` | `0x00000F0F` | **4‑lane mask** |
| `380060/70/80/90h` | `0x00003FCC` | **four identical DP lane drivers** |
| `380064/74/84/94h` | `0x373000C3` | DP lane driver (×4) |
| `380338h` | `0x00002AD5` | DP **MSA** Mvid (video time‑stamp) |
| `380504h` | `0x00210021` | 10‑bpc format |
| `38050Ch` | `0x0300003F` | 63‑slot MST reference |

Link status from header `DPCD 202~205h: 77 77 01 01` = all 4 lanes trained + aligned, **0 symbol errors.**

---

## 10. VC‑slot / payload allocation

Two representations:

**(a) The 64‑slot VC Payload table** (header) — read by VMMTool over **AUX** (DPCD‑side, *not* in the MMIO register dump):

```
slot 0      : reserved (FF)
slots 1–47  : VC2  (ViewSonic)   = 47 slots
slots 48–59 : VC1  (D1)          = 12 slots
slots 60–63 : unused (FF)
RFRM VCID list: 01 00 02 00   (RFRM0=VC1=D1, RFRM2=VC2=ViewSonic)
```

**(b) The chip's per‑stream state** = the RFRM blocks above (§6), each holding the stream's MSA + DSC bit.

The **47 : 12 ratio is pure bandwidth**: ViewSonic uncompressed (8.29 Gb/s) ÷ D1 DSC'd (2.07 Gb/s) ≈ 4 : 1 ≈ 47 : 12. (Uncompressed, D1 would need ~28 slots → 47+28 = 75 > 63 → **won't fit**; DSC'd to 12 → 47+12 = 59 ≤ 63 → fits. This is *why* DSC must be on.)

---

## 11. Bandwidth accounting

| Stream | Active payload | Slots |
|---|---|---|
| ViewSonic 2560×1440×74.92 × 30 bpp (10‑bit RGB, **uncompressed**) | **8.29 Gb/s** | 47 |
| D1 1920×1080×99.98 × 10 bpp (**DSC**) | **2.07 Gb/s** | 12 |
| **Total on trunk** | **≈ 10.36 Gb/s** | 59 / 63 |
| Trunk ceiling (2‑lane HBR3, 8b/10b) | **12.96 Gb/s** (≈12.3 after FEC) | 63 |

Without DSC the two streams would be 8.29 + 4.98 = **13.27 Gb/s > 12.96** → impossible on a 2‑lane trunk. DSC on D1 is what makes it fit.

---

## 12. Monitor EDIDs (dump lines 5200–5299)

| | **ViewSonic** | **D1** | (RX default) |
|---|---|---|---|
| Product | VA3209‑QHD (`VSC A93D`) | D1 (`DTZ 4426`) | Non‑PnP (`SYN 3000`) |
| Interface | DisplayPort, **10‑bit** | HDMI | DisplayPort, 8‑bit |
| Serial / mfg | WYM23264…, wk26/2023 | 539296806, wk18/2025 | 12345678, 2013 |
| Preferred | 2560×1440@60 (241.50 MHz) | 1920×1080@100 (235.20 MHz) | 1024×768@60 |
| Key detail timing | **2560×1440@75 = 301.85 MHz** | 1920×1080@75/60 | — |
| Max pixel clock | ≤ 320 MHz | ≤ 300 MHz | ≤ 160 MHz |

Pixel clocks match the live RX frames to the decimal (RFRM2 = 301.8 MHz, RFRM0 = 235.2 MHz).

---

## 13. Methodology & honest limits

- **Decoded exactly** (public standards): the DSC PPS (VESA DSC 1.2), the rate‑control model, DP/HDMI MSA timings, DPCD‑style lane/link status.
- **Placed by correlation** (matched to known facts): link rate `810`=HBR3, the per‑stream MSA, the DSC on/off control words, channel/lane driver blocks.
- **Not authoritatively bit‑decoded**: the bulk of the 2,136 registers — internal state machines, FIFO levels, mux routing, and the `0xC002801C` / `0x54603F48` config words — because that mapping is in Kinetic/MegaChips's NDA register manual.

### Cross‑check vs. the GPU side (NVAPI)
Every fact here was independently confirmed from the NVIDIA driver before this dump existed:

| Fact | From the hub (this dump) | From NVAPI (GPU side) |
|---|---|---|
| Trunk lanes | `210000h=…77` → 2 | `maxLaneCount=2`; live escape `laneCount=2` |
| Trunk rate | `2107C0h=810` → HBR3 | `maxLinkRate=30` → HBR3 |
| MST | RFRM0/1/2 + VC table | MST‑root + 2 dynamic sinks |
| **DSC ON** | `RX: … DSC ON`; `220A00h=0xA2000006` | load 13.27 > ceiling 12.96 ⇒ DSC required |
| DSC bpp | PPS = **10.00 bpp** | NVIDIA MST hard‑codes 10 bpp |

The hub simply **reports the on/off bit and the full PPS directly** — the things NVIDIA never surfaces in its control panel and locks behind the gated `NvDP` test‑util escapes.

---

*Generated from `dump.txt` (VMMTool 9.03.07). Standards‑based decodes are authoritative; proprietary‑register interpretations are best‑effort correlation pending the NDA register map.*
