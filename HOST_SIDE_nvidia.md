# Host side — driving the dock's displays from the NVIDIA GPU

The rest of this repo reads the dock's silicon (the VMM5310 MST hub over DP AUX,
the VL8xx USB hubs over usbfs). This file is the *other* end of the same link:
how the **host** — an NVIDIA GPU running the open-source `nvidia` kernel driver —
feeds pixels into that hub, and what had to change in the driver to (a) tune the
DSC compression it sends, (b) stop it deadlocking the machine, and (c) stop its
firmware's fence-completion events from silently going missing.

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

## 5. The residual freeze: lost firmware events, and a second wakeup path

With the unified engine live, one symptom remained: **occasional multi-second
eDP freezes that self-recover** — silent, nothing in dmesg. The engine admits
exactly one story with that shape. A pending prime fence has three wakeups:
the NVKMS channel event (fires per semaphore write), a later fence attach on
the same context (drains against the live payload), and the one-shot timer at
creation + 5 s. Drop the channel event for the *last* frame before the eDP
goes idle and the first two go silent; the fence sits until the timer fires,
reads the long-since-advanced payload, and signals *success*. A ~5 s freeze
with no error anywhere. Mid-activity drops are masked within a frame by the
next event or attach — hence "occasional".

Tracing the delivery chain through the driver source settled where the drop
lives:

- **The prime channel event originates inside GSP-RM firmware.** It is a
  persistent `NV01_EVENT_KERNEL_CALLBACK_EX` on the producing channel; the
  semaphore-release-with-AWAKEN is noticed by GSP-RM, which posts a
  `POST_EVENT` RPC to the CPU (`_kgspRpcPostEvent`) → `osNotifyEvent` → the
  nvidia-drm callback, directly — no re-arm, no queueing, no coalescing,
  nothing CPU-side that can drop it. A loss therefore means the firmware
  never posted, which is not fixable from source. Measured rate on this
  machine: **about one lost event per ~12,000** (first logged freeze:
  `ch_ev=11691`).
- **Adjacent finding (real, but a different victim): the GA10x plain-MSI
  re-arm window.** The non-stall interrupt tree — which carries the
  *semaphore-surface* (Vulkan explicit-sync) fence wakeups — is serviced in
  a single lockless top-half pass with no re-scan anywhere, and on GA10x
  with plain MSI its only re-arm is a config-space EOI write; the explicit
  `intrRetriggerTopLevel()` (which force-toggles the non-stall subtree
  enables so latched-but-unserviced state re-fires) is invoked only for
  MSI-X. GH100+ moved off the EOI scheme entirely; GA10x is still on it in
  upstream main as of 2026-07. This laptop runs plain MSI.
- **Ruled out: rebuilding prime signaling on RM's SemaphoreSurface waiters**
  (the transactional register-with-`ALREADY_SIGNALLED` API). It rides the
  same non-stall interrupt underneath, and that interrupt is generated by
  GPU conditional-trap methods only a semsurf-aware command stream emits,
  bound via a channel-bind control nothing kernel-side can reach — a
  kernel-built surface over the prime semaphore memory would never fire.

The fix — fork commits `b9aa06d`, `aab24a4`, `c1fadc5` — is a **second,
GSP-independent hardware wakeup**, plus hardening and instrumentation:

1. **MSI re-arm hardening** (`b9aa06d`): the plain-MSI branch of
   `kbifCheckAndRearmMSI()` now also calls `intrRetriggerTopLevel()` after
   the config-space EOI — parity with the MSI-X branch and with post-Ampere
   chips. Primary beneficiary is the semsurf/Vulkan path; cost is a few
   extra MMIO writes per interrupt.
2. **Redundant non-stall wakeup for prime fences** (`aab24a4`): the same
   semaphore release the firmware should report also raises the **host
   non-stall interrupt** (`FIFO_EVENT_MTHD`), serviced by CPU-RM directly —
   and on GA100+ every serviced engine non-stall edge additionally
   broadcasts to the HOST notifier list. The KAPI now registers a
   subdevice-parented kernel callback there for each prime fence context;
   the fence handler peeks its pending list first (one spinlock on an idle
   timeline, since the broadcast also fires for unrelated traffic) and runs
   the usual level-triggered drain. A lost firmware event is recovered by
   the next non-stall edge — same frame — instead of the 5 s timer. Also:
   per-context delivery counters and sparse logs, so previously-silent
   timer recoveries now print with totals.
3. **Registration lifecycle fix** (`c1fadc5`) — found *by* those
   diagnostics on the first post-deploy freeze, which logged
   `timer=1 ch_ev=11691 ns_kick=0 ns_idle=0`: eleven thousand channel
   events, zero non-stall deliveries on the hot context, while the boot
   log's "path is live" line proved the mechanism worked… for an earlier,
   already-destroyed context. Root cause: the `SET_NOTIFICATION` arming
   state lives on the *shared subdevice object*; RM rejects an armed→armed
   transition with `INVALID_STATE`, the original registration treated that
   benign answer as fatal (freeing the event), and teardown's unconditional
   `DISABLE` disarmed any survivor — so every fence context after the first
   ran unprotected. Fixed by accepting `INVALID_STATE` (delivery through
   the engine notification list is not gated by the arming at all — RM's
   own `sem_surf.c` never calls `SET_NOTIFICATION`) and never disabling on
   free.

**Validated live:** every fence context now announces
`prime fence: redundant nonstall wakeup path is live` on its first
non-stall delivery — including freshly churned ones (forcing the eDP
through a mode change and back created three new contexts; all announced
within seconds), which is exactly the case that was broken. Residual
exposure is a simultaneous miss of both event paths, still bounded by the
5 s `-ETIMEDOUT` backstop — and it would now log itself with counters
instead of passing as an unexplained freeze.

*Noted in passing, not fixed here:* NVKMS-core defers its own
non-stall/hotplug/DP-IRQ callbacks through a `GFP_ATOMIC` allocation whose
failure is silently ignored (`(void)nvkms_alloc_timer_with_ref_ptr(...)` in
`nvkms-rm.c`) — a silent event drop under memory pressure, upstream-report
material.

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
particular host had to do to drive that silicon well. The DSC defines, the
fence engine, and the interrupt/event plumbing are NVIDIA `nvidia-open`
internals; the fork carries the exact diffs and the reasoning is in its commit
messages. The firmware-side event loss itself remains out of reach — GSP is a
binary blob — so the fix is redundancy and accounting around it, not a cure.
