# Host side — driving the dock's displays from the NVIDIA GPU

The rest of this repo reads the dock's silicon (the VMM5310 MST hub over DP AUX,
the VL8xx USB hubs over usbfs). This file is the *other* end of the same link:
how the **host** — an NVIDIA GPU running the open-source `nvidia` kernel driver —
feeds pixels into that hub, and what had to change in the driver to (a) tune the
DSC compression it sends and (b) stop it deadlocking the machine.

This is a different domain from the dock RE: it is host GPU-driver work, specific
to this laptop (ASUS ROG Strix G533QS — RTX 3080 Laptop + AMD Cezanne iGPU, the
dock's USB-C wired to the NVIDIA side), on `nvidia-open` **610.43**. All driver
changes live in a fork, not in this repo:

> **github.com/gt4o4/open-gpu-kernel-modules**, branch `610.43.02-dock`
> (upstream tag `610.43.03` relabelled to match the 610.43.02 userspace/GSP).

The findings below are **proven from live measurement** unless flagged otherwise:
the hub register/slot readouts come from `vmmdump` (this repo), the PCIe and
scanout numbers from `nvidia-smi`/`debugfs`, and the deadlock from a captured
`SysRq-w` task dump.

## 1. DSC compression rate

D1 (a 1920×1080@100 8-bit panel) reaches the GPU only as a DSC-compressed MST
stream that the VMM5310 decodes before driving the panel. The compression ratio
is set host-side. The NVIDIA DP library picks the MST DSC bits-per-pixel from two
`#define`s in `src/common/displayport/inc/dp_deviceimpl.h`
(`PREDEFINED_DSC_MST_BPPX16`, `MAX_DSC_COMPRESSION_BPPX16`).

The trunk is HBR3 ×2 lanes = 12.96 Gb/s of 8b/10b payload, a 64-slot MTP with 63
usable slots at ~202.5 Mb/s each (see `vmmdump --slots`). The budget math, all
confirmed against live slot-table readouts:

| stream | mode | slots |
|---|---|---|
| ViewSonic 2560×1440@75, 10-bit | **uncompressed** | 48 |
| D1 1920×1080@100, 8-bit | DSC **10.0 bpp** (stock, 2.4:1) | 12 |
| D1 same | DSC **12.0 bpp** (patched, 2.0:1) | 15 |

12.0 bpp gives D1 20% more bits/pixel and the pair lands at exactly **63/63**
slots. Raised `PREDEFINED_DSC_MST_BPPX16` 160→192 and the fallback floor
`MAX_DSC_COMPRESSION_BPPX16` 128→160 (never fall below the known-good 10.0 bpp).
The hub only accepts whole-bpp targets (DPCD `0x6F` bit 2 = 1-bpp increments).

**Proven-by-experiment aside — the hub under-reports its DSC decoder.** The
VMM5310's DPCD `0x6A = 0x02` advertises an 8-bit-only DSC decoder, but the
silicon decodes 10 bpc fine: with a one-line patch force-advertising 10-bit
decode, the driver DSC-compressed the *10-bit* ViewSonic stream (PPS read back:
10 bpc, 11.0 bpp, 2.7:1) and the hub decoded it to a stable picture for ~30 min.
Not deployed — at 2.7:1 the subpixel-text artifacts are visible, and leaving the
10-bit monitor uncompressed is better here. Kept on the fork's
`610.43.02-dock-10bpc-advertise` branch as a documented experiment.

*Inferred, not proven:* that the same defines govern every MST DSC stream (we
only observed D1 + this one monitor pair).

## 2. Reverse-PRIME and the bandwidth floor

The dock's USB-C is wired to the NVIDIA GPU, so its DP-MST heads are NVIDIA
outputs while the 300 Hz internal eDP is on the AMD iGPU. Two arrangements:

- **AMD primary (default), NVIDIA sink:** the NVIDIA display engine scans the
  dock framebuffers out of *system RAM* every refresh → a permanent
  **~1.8–2.4 GB/s PCIe RX floor** even on a static desktop (measured), and the
  NVIDIA DDX refuses synced flips on the MST heads (tearing/stutter).
- **NVIDIA primary, AMD sink (switched to, via an `xorg.conf.d` `PrimaryGPU`
  OutputClass):** dock heads become native NVIDIA outputs scanned from VRAM →
  idle PCIe RX drops to **~1–44 MB/s** (measured). The eDP becomes a reverse-
  PRIME sink; per `debugfs`, amdgpu's TearFree blits the shared buffer into a
  local tiled double-buffer pair and page-flips from the iGPU's DRAM carve-out,
  so **PCIe is not in the eDP scanout path** and the internal panel is tear-free
  without PRIME sync.

## 3. The deadlock (why NVIDIA-primary "didn't work" before)

Switching to NVIDIA-primary reliably wedged amdgpu within minutes. Root cause,
confirmed from a `SysRq-w` dump: NVIDIA's implicit-sync ("prime") dma-fences —
which an amdgpu reverse-PRIME sink waits on before scanout — signal **only** from
an NVKMS channel-event callback, with no bounded-time guarantee. On an idle
timeline a lost/late event leaves the fence unsignaled forever; and on kernel
7.0 the driver's `dma_fence_ops.wait` 96 ms safety-clamp is no longer honoured
(the same `dma_fence_ops` reshape that dropped `use_64bit_seqno`), so even plain
blocking waiters hang. Captured stacks:

```
kworker/.../commit_work: drm_atomic_helper_wait_for_fences
    -> dma_fence_wait_timeout -> dma_fence_default_wait   [blocked >122s]
systemd: ...exec -> filp_close -> amdgpu_flush
    -> drm_sched_entity_flush                              [victim, same fence]
```

The trace is archived at `/var/tmp/deadlock_trace_stock_fences_*.txt` on the box.

## 4. The fix

The driver already ships a *second*, correctly-bounded fence class — the
semaphore-surface ("semsurf") fences used for Vulkan/Wayland explicit sync, with
a per-context workthread + one-shot timer and a 5 s `-ETIMEDOUT` backstop.

1. **Unify** both classes onto that one timeout engine (fork commit `00779c7`):
   prime fences now ride the semsurf timer instead of hanging. Shared
   pending-list / drain / force-complete / timeout, with a 2-op vtable
   (`read_seqno`, `update`) for the parts that genuinely differ (NvKms backend,
   32- vs 64-bit seqno). UAPI unchanged (all 7 fence ioctls identical).

2. **Fix the review findings** (fork commit `812fed8`), from three independent
   adversarial code reviews of the unified engine:
   - a **double-free/UAF** in both fence-context create ioctls (a redundant
     `destroy` after the ref-drop already frees — pre-existing upstream);
   - a **hot-path hang** where a reordered stale timer arm + the re-arm dedup
     could permanently drop the 5 s backstop (decoupled the timer deadline from
     the dedup so it self-heals);
   - a **teardown UAF** where the prime channel event armed the timer directly
     (now deferred to the worker, which teardown drains first);
   - a low-likelihood **seqno-wrap** premature-signal (made the wrap flush
     atomic with the list insert).

Net result, verified live after reboot: NVIDIA-primary runs the reverse-PRIME
eDP path with no hang/fence-timeout in `dmesg`, idle PCIe near zero, and the hub
side unchanged (D1 at 12.0 bpp, ViewSonic uncompressed, 63/63 slots).

## Deploy notes (this machine)

- Built + signed via DKMS for the two live kernels (7.0.0-28, 7.0.0-14).
- **Kernel-7.0 gotcha:** `dkms install` does *not* refresh the initramfs, and
  with early KMS the initramfs copy of `nvidia-drm.ko` wins at boot — so every
  driver iteration needs `update-initramfs -u -k <ver>` before reboot, else the
  stale module loads. (This bit us once; verify with
  `lsinitramfs … | modinfo -F srcversion`.)
- Rollback: `git revert` on the fork, or the original blob driver backed up at
  `/usr/src/nvidia-610.43.02.blob.bak`.

## Honest limits

Host-side, driver-specific, single-machine. None of this is part of the dock's
own silicon (which is what the rest of the repo reverse-engineers) — it is what a
particular host had to do to drive that silicon well. The DSC defines and the
fence engine are NVIDIA `nvidia-open` internals; the fork carries the exact diff
and the reasoning is in its commit messages.
