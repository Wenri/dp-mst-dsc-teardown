# DisplayPort MST + DSC teardown — what a USB‑C dock actually does to your monitors

Ground‑truth reverse‑engineering of the DisplayPort Multi‑Stream Transport (MST) hub inside a USB‑C dock — a **Kinetic / MegaChips VMM5310** (the "Panamera" VMM53xx family) — fanning a single laptop GPU output out to two external monitors.

This repo is two files and the story that connects them:

- **[`dump.txt`](dump.txt)** — the raw register + EDID dump read out of the hub over the DisplayPort **AUX** channel with MegaChips' own **VMMTool** diagnostic. 2,136 registers, three EDIDs. This is the ground truth: bits read off the silicon, not inferred.
- **[`VMM5310_dump_decoded.md`](VMM5310_dump_decoded.md)** — a full decode of that dump. Every block I could place is placed, every value annotated with where it came from, and an explicit line drawn between *proven from the bits* and *inferred by correlation*.

## The setup

| | |
|---|---|
| **Host** | ASUS ROG G533QS — RTX 3080 Laptop GPU + Ryzen 9 5900HX iGPU |
| **Dock** | USB‑C, containing the VMM5310 DP MST hub |
| **Monitor A** | ViewSonic VA3209‑QHD — 2560×1440 @ 75 Hz, 10‑bit, over DisplayPort |
| **Monitor B** | a 1080p panel ("D1") — 1920×1080 @ 100 Hz, over HDMI |

## What the silicon actually says

The hub receives one MST trunk from the GPU and demuxes it to two displays. Decoded from the dump:

- **Trunk in:** 2‑lane **HBR3** (8.1 Gb/s/lane) MST, FEC on, DSC on. *(`210000h`, `2107C0h` = 810)*
- **ViewSonic branch:** 4‑lane **HBR2** DisplayPort, **uncompressed**, full 10‑bit — rides on its own branch with bandwidth to spare.
- **D1 / HDMI branch:** **DSC‑compressed** — VESA DSC 1.2, **10.00 bpp**, 1920×1080 picture/slice, **2.4:1**. *(full Picture Parameter Set decoded in §7 of the decode)*
- **Bandwidth:** ≈10.36 Gb/s carried on a 12.96 Gb/s ceiling. Uncompressed, the same two streams want ≈13.27 Gb/s — over the ceiling. So **DSC here isn't a quality knob, it's load‑bearing**: turn it off and a monitor goes dark.

The surprise the dump settled: it's the **8‑bit HDMI 1080p stream that's compressed**, while the 10‑bit ViewSonic rides clean. From the GPU side that allocation is invisible — and not yours to change.

## Why bother

The GPU vendor decides which stream gets compressed and never tells you. The dock's hub knows exactly what's happening on every lane — it just doesn't ship a UI. VMMTool plus a register manual's worth of public standards (VESA DSC, DP/HDMI MSA) turns the hub into the instrument the driver refuses to be. You don't get *control* this way. You get *truth*, which is the prerequisite for everything else.

## Honest limits

Standards‑defined fields — the DSC Picture Parameter Set, MSA timings, DP link status, the rate‑control model — decode exactly. Chip‑proprietary registers are placed by correlation against the known‑good VMMTool header and would need Kinetic's NDA register manual to call definitively. The decode says which is which; §13 is where that line is drawn.

## Method

`VMMTool.exe` (MegaChips' diagnostic) reads the hub's registers over the DP AUX channel. The decode cross‑references those bytes against the public DisplayPort, HDMI, and VESA DSC specifications, and against an independent NVAPI‑side investigation of the same link from the GPU (cross‑check table at the end of the decode).

## License

[WTFPL](LICENSE) — *Do What The Fuck You Want To Public License*, v2. Reverse-engineer it, copy it, mirror it, build on it, or paste it into your own dock investigation. No strings.

A repo about reclaiming control of your own hardware deserved a license that just says *do what you want with it.*

**No warranty, though** — the dump, EDIDs, and analysis are provided **as-is**:

> THE WORK (including the register dump, EDID data, and all analysis herein) IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE WORK OR THE USE OR OTHER DEALINGS IN THE WORK.

So: do what you want with it, but if your dock smokes, that's on you.

---

*Captured from my own hardware. The register and EDID data is what the chip reported about my two monitors; there are no credentials or secrets in it. Shared in the hope it's useful to the next person trying to find out what their dock is really doing.*
