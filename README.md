# DisplayPort MST + DSC teardown — what a USB‑C dock actually does to your monitors

Ground‑truth reverse‑engineering of the DisplayPort Multi‑Stream Transport (MST) hub inside a USB‑C dock — a **Kinetic / MegaChips VMM5310** (the "Panamera" VMM53xx family) — fanning a single laptop GPU output out to two external monitors.

This repo covers **both halves** of the dock — the DisplayPort side and the USB side — and the story that connects them:

- **[`dump.txt`](dump.txt)** — the raw register + EDID dump read out of the hub over the DisplayPort **AUX** channel with MegaChips' own **VMMTool** diagnostic. 2,136 registers, three EDIDs. This is the ground truth: bits read off the silicon, not inferred.
- **[`VMM5310_dump_decoded.md`](VMM5310_dump_decoded.md)** — a full decode of that dump. Every block I could place is placed, every value annotated with where it came from, and an explicit line drawn between *proven from the bits* and *inferred by correlation*.
- **[`vmmdump/`](vmmdump/)** — the DP capture, reproduced: a Python tool that reads the same hub live from **Linux** over DP AUX — even through the NVIDIA proprietary driver, which exposes no AUX device nodes at all. It re-verified the Windows dump register for register.
- **[`vlidump/`](vlidump/)** + **[`USB_side_decoded.md`](USB_side_decoded.md)** — the *other* half: the **VIA Labs VL822/VL817** USB hub (and its VIA Labs USB-C PD controller) that carries USB 3 over the Type-C cable's other lane pair. `vlidump` reads it live over plain USB; the decode maps its firmware flash. The dock splits its four Type-C lanes 2 (DP) + 2 (USB) — which is *why* DSC is load-bearing on the DP side.
- **[`HOST_SIDE_nvidia.md`](HOST_SIDE_nvidia.md)** — the *host* end of the same link: tuning the DSC compression the NVIDIA GPU sends into the hub (10→12 bpp; and a live proof the hub decodes 10 bpc despite its DPCD claiming 8-bit-only), plus two real driver bugs run to ground on the reverse-PRIME path — a kernel deadlock, and GSP firmware fence events that occasionally vanish (~1 in 12,000, each one a silent 5-second freeze; fixed with a redundant interrupt-driven wakeup). Driver changes live in a fork; this is the writeup and the reasoning.

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

## Reproducing it on Linux: `vmmdump`

[`vmmdump/`](vmmdump/) re‑captures everything in `dump.txt` live, without Windows or VMMTool: chip/firmware identity, the full register space, the decoded link/stream/DSC view, and the monitor EDIDs.

The catch it solves: on a dock wired to an NVIDIA GPU there is no `/dev/drm_dp_aux*` — the NVIDIA driver (proprietary *and* open) never creates those nodes, so the usual Linux path to a Synaptics MST hub's DPCD is a dead end. `vmmdump` instead issues native AUX transactions through the driver's Resource‑Manager ioctl ABI (`NV0073_CTRL_CMD_DP_AUXCH_CTRL` on `/dev/nvidiactl`, root‑only, stock driver). A `/dev/drm_dp_aux*` backend covers amdgpu/i915/nouveau hosts. Reads only — it never writes the hub's flash.

```sh
sudo python3 -m vmmdump --edid               # live: identity + decode + EDIDs
sudo python3 -m vmmdump --slots              # live: MST trunk + VC payload slot table
python3 -m vmmdump --decode-file dump.txt    # offline: decode this repo's dump
```

Read live and diffed against the Windows capture: identity exact, every register the decoder interprets identical, ~92 % of the full 2,141‑register dump byte‑identical — and 100 % of the differences sit in live‑adaptive fields (PHY equalizer taps, training status, counters) that no two captures share. Details, usage, and the verification write‑up: [`vmmdump/README.md`](vmmdump/README.md).

## Reference material

The [`reference/`](reference/) folder holds the third-party documents this teardown leans on:

- `VMM5320.pdf`, `VMM5330.pdf` — MegaChips / Kinetic VMM53xx datasheets (the family the VMM5310 belongs to).
- `HD-G218_User_manual.pdf` — the dock's own manual.

**These are not mine and not under the WTFPL** — they remain under their original owners' copyright and are bundled here for reference only. See [`NOTICE`](NOTICE). Rights holders who want a file pulled: open an issue and it's gone.

## License

[WTFPL](LICENSE) — *Do What The Fuck You Want To Public License*, v2. Reverse-engineer it, copy it, mirror it, build on it, or paste it into your own dock investigation. No strings.

A repo about reclaiming control of your own hardware deserved a license that just says *do what you want with it.*

**No warranty, though** — the dump, EDIDs, and analysis are provided **as-is**:

> THE WORK (including the register dump, EDID data, and all analysis herein) IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE WORK OR THE USE OR OTHER DEALINGS IN THE WORK.

So: do what you want with it, but if your dock smokes, that's on you.

---

*Captured from my own hardware. The register and EDID data is what the chip reported about my two monitors; there are no credentials or secrets in it. Shared in the hope it's useful to the next person trying to find out what their dock is really doing.*
