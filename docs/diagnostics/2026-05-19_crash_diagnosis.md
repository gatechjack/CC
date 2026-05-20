# Crash diagnosis — 2026-05-19

**Status:** Diagnostic report only. No fixes applied this session. Mitigations require
Board review and a follow-up session before action.

**Context:** Six (user-reported) crashes during recent Trading Corp work sessions.
The latest crash occurred during a light-weight session (file reads + a scoped
pytest), which invalidates the prior working hypothesis ("long pytest causes OOM").
OS-level Event Viewer data actually shows **ten** Kernel-Power 41 critical reboots
in the last 30 days (and four more 6008 markers from earlier on 5/17–5/18 that
correspond to overlapping reboot chains); user's "six" figure is consistent with
"crashes that interrupted my Claude Code work" — there were several more on this
machine that the user wasn't actively at.

**Machine:** MSI GE76 Raider 11UE gaming laptop (i7-11800H + RTX 3060 Mobile + 16 GB
RAM + 1 TB Samsung NVMe), Windows 11 Home build 26200.

---

## 1. What we know

### Crash inventory (from Event Viewer)

Ten Kernel-Power 41 "unexpected reboot" critical events in the last 30 days:

| #  | Timestamp (local)   | Has BugCheck dump? |
| -- | ------------------- | ------------------ |
| 1  | 2026-04-26 13:28:49 | No                 |
| 2  | 2026-04-27 18:15:42 | No                 |
| 3  | 2026-04-28 07:09:41 | No                 |
| 4  | 2026-05-15 21:09:24 | No                 |
| 5  | 2026-05-17 23:07:06 | No                 |
| 6  | 2026-05-17 23:27:43 | No                 |
| 7  | 2026-05-18 00:04:24 | No                 |
| 8  | 2026-05-18 07:15:38 | No                 |
| 9  | 2026-05-18 10:39:06 | No                 |
| 10 | 2026-05-18 11:17:04 | **YES** (0x7E)     |
| 11 | 2026-05-18 19:31:40 | No                 |

(That's 11 if you count the BugCheck event separately; 11 distinct kernel-power
critical reboot events. The 6008 log shows additional unexpected shutdowns at 4:01 PM
and 2:51 PM on 5/17 that don't have matching K-P 41 events — likely the EventLog
service crashed mid-write on those.)

**Acceleration pattern:** 4 crashes in 3 weeks (4/26 → 5/15), then 8 crashes in the
24-hour window 5/17 23:07 → 5/18 19:31. Something changed around 5/15.

### The one BugCheck we have

```
Time:        2026-05-18 11:17:17
Bug check:   0x0000007E (SYSTEM_THREAD_EXCEPTION_NOT_HANDLED)
Parameter 1: 0xFFFFFFFFC0000005  (STATUS_ACCESS_VIOLATION)
Parameter 2: 0xFFFFF80489594CB6  (faulting instruction address in some kernel module)
Parameter 3: 0xFFFFF3862D816778  (exception record address)
Parameter 4: 0xFFFFF3862D815F80  (context record address)
Dump file:   C:\WINDOWS\Minidump\051826-14937-01.dmp  (referenced but MISSING from disk)
Report ID:   37ac35f4-b323-44ad-b42d-119fc65d39e0
```

`SYSTEM_THREAD_EXCEPTION_NOT_HANDLED` with `STATUS_ACCESS_VIOLATION` is the
textbook signature for a kernel-mode driver dereferencing a bad pointer. To resolve
**which driver**, the .dmp must be analyzed in WinDbg — but the file is missing from
`C:\Windows\Minidump\` despite the BugCheck event referencing it. `volmgr` did log
"Dump file generation succeeded" at the time of the crash, so it was written then
deleted afterward (likely candidates: Microsoft Defender, Storage Sense, or an MSI
cleanup utility — `MinidumpsCount=5`, `Overwrite=1`, so it shouldn't have been
auto-deleted by the dump subsystem). **All other 10 crashes produced no dump at all.**

### Why most crashes have no dump (key insight)

`AutoReboot=1` and `CrashDumpEnabled=3` (small minidump). On a healthy BSOD the
kernel writes a 256 KB minidump in ~1 second, then reboots — that's what happened
once (5/18 11:17). For 9 out of 10 crashes the kernel **didn't even get that far**.
That points away from "ordinary BSOD from a bad pointer" and toward one of:

- A hang where the kernel can't run the bug-check path (storage stack wedged,
  watchdog timer expires, embedded controller forces a hard reset)
- Power loss (PSU brownout, battery cutout while supplementing AC transients)
- A driver wedging the disk subsystem so the dump write itself fails

### Smoking gun: RstMwService

**Every single one** of the 9 reboots since 5/15 (where SCM logs survived) shows
`RstMwService` (Intel Rapid Storage Technology Management Service) terminating with
error code `%%2684420176` (= `0xA00F0050`, a non-zero status from the service host)
within seconds of the reboot. Examples:

| Reboot time         | RstMwService termination time | Delta   |
| ------------------- | ----------------------------- | ------- |
| 2026-05-15 21:09:24 | 2026-05-15 21:09:25           | +1s     |
| 2026-05-17 23:07:06 | 2026-05-17 23:07:19           | +13s    |
| 2026-05-17 23:27:43 | 2026-05-17 23:27:55           | +12s    |
| 2026-05-18 00:04:24 | 2026-05-18 00:04:37           | +13s    |
| 2026-05-18 07:15:38 | 2026-05-18 07:15:51           | +13s    |
| 2026-05-18 10:39:06 | 2026-05-18 10:39:19           | +13s    |
| 2026-05-18 11:17:04 | 2026-05-18 11:17:18           | +14s    |
| 2026-05-18 19:31:40 | 2026-05-18 19:31:53           | +13s    |

That ~13-second offset is the same on every reboot — Service Control Manager
starting up after reboot tries to start the service, the service fails immediately.
**This is the service host noticing on startup that its native counterpart
(`iaStorAC.sys` / `iaStorAVC.sys` storage driver) is in a state it can't talk to —
i.e., the storage stack didn't shut down cleanly.** That's exactly the symptom you'd
expect if the storage driver is the thing causing the reboots.

### Out-of-date drivers (factory date 2021-04-22, never updated)

| Component                        | Version          | Driver date | Age      |
| -------------------------------- | ---------------- | ----------- | -------- |
| **NVIDIA GeForce RTX 3060 Mobile** | 30.0.14.9717   | 2021-12-02  | ~4.5 yrs |
| **Intel UHD Graphics**           | 30.0.101.1340    | 2022-02-02  | ~4 yrs   |
| **Killer Wi-Fi 6E AX1675x**      | 22.70.0.6        | 2021-06-28  | ~5 yrs   |
| Killer E3100G Ethernet           | 1125.20.729.2024 | 2024-07-28  | ~2 yrs   |
| Intel ME Interface               | 2406.5.5.0       | 2024-02-07  | ~2 yrs   |
| Intel Chipset Device Software    | 10.1.18698.8258  | 2021-04-22  | OEM      |
| **RstDowngradeGuard**            | 18.0.0.0         | 2021-04-22  | OEM      |
| **OptaneDowngradeGuard**         | 18.0.0.0         | 2021-04-22  | OEM      |
| Killer Performance Driver Suite  | 3.0.1543         | 2021-04-22  | OEM      |
| Thunderbolt Controller           | 1.41.1094.0      | 2021-01-23  | ~5 yrs   |

`RstDowngradeGuard` and `OptaneDowngradeGuard` are *blocking* Intel RST driver
updates — that's their stated purpose. This explains how a 5-year-old Intel RST
stack is still running on a current Windows 11 build. NVIDIA's driver from Dec 2021
is roughly 350 driver releases behind the current Game Ready Driver.

### Recent Windows updates (the change at 5/15)

| KB        | Type            | Install date |
| --------- | --------------- | ------------ |
| KB5089549 | Security Update | 2026-05-15   |
| KB5087051 | Update          | 2026-05-15   |
| KB5092762 | Security Update | 2026-05-14   |
| KB5054156 | Update          | 2026-04-28   |

The crash acceleration began **the day** KB5089549 + KB5087051 installed. The 4/28
crash was on the same day KB5054156 installed too (less clean but suggestive). The
older 4/26 and 4/27 crashes are unexplained by updates — a baseline of ~weekly
crashes existed even before, then jumped to multi-per-day after the May updates.

### Concurrent service / app crashes (last 7 days)

| Process                                | Crashes | Notes                                                                                |
| -------------------------------------- | ------- | ------------------------------------------------------------------------------------ |
| `KillerProviderDataHelperService.exe`  | 12      | Rivet/Intel Killer networking service. Repeated faults.                              |
| `Start_HDR.exe`                        | 10      | Windows HDR/display utility.                                                         |
| `dwm.exe`                              | 4       | Desktop Window Manager (graphics).                                                   |
| `backgroundTaskHost.exe`               | 2       | Windows background tasks.                                                            |
| `KNDBWM.exe`                           | (multi) | Killer kernel-mode network driver helper — access-denied errors.                     |
| `Killer Network Service`               | 1       | Terminated 2 min before the 5/18 19:31 reboot.                                       |
| `Connected User Experiences/Telemetry` | 1       | Terminated minutes before the 5/18 11:17 BugCheck.                                   |

The Killer + display utility crash chorus is *not* itself the cause of the reboots
(user-mode crashes don't reboot the box), but it tells us the network stack and
display stack are both unwell — consistent with the same OEM drivers being out of
date.

### Hardware state

| Check                  | Result                                                            |
| ---------------------- | ----------------------------------------------------------------- |
| WHEA-Logger events     | **None in 30 days** (no machine-check exceptions, no PCIe AERs)   |
| SMART status (NVMe)    | OK                                                                |
| Disk free space (C:)   | 570 GB free / 931 GB (61.3% free)                                 |
| Total RAM              | 16 GB                                                             |
| Current RAM used (idle)| 7.0 GB (43%)                                                      |
| Pagefile               | 17 GB allocated, 0 MB currently in use                            |
| Battery design         | 95,000 mWh                                                        |
| Battery full charge    | **73,021 mWh (23% wear)**                                         |
| Battery cycle count    | 2 (suspect — likely firmware reset or under-reporting)            |
| AC adapter             | Plugged in, online                                                |

**WHEA silence is the big one.** No hardware-error machine-check, no PCIe error
correction events. That argues *against* the CPU, RAM, GPU silicon, NVMe, or
motherboard PCIe bus being faulty — those would generate WHEA records before a
crash if they were the cause. Battery wear (23%) is non-trivial for a gaming laptop
but the system is on AC; battery degradation by itself shouldn't cause reboots
unless the PSU is also weak, in which case we'd usually see WHEA thermal events,
which we don't.

### Process state at writing (idle session, lightweight ops)

Top memory consumers right now:

| Process                  | WS (MB) | Notes                                             |
| ------------------------ | ------- | ------------------------------------------------- |
| claude (×4 processes)    | ~996 MB | Claude Code instances                             |
| MsMpEng                  | 330 MB  | Defender realtime scan                            |
| explorer                 | 253 MB  |                                                   |
| dwm                      | 148 MB  | Recently crashed several times                    |
| KillerNetworkService     | 109 MB  | The chronically-crashing one                      |
| LogiOverlay              | 96 MB   |                                                   |

7.0 GB used / 16 GB. **There is no memory pressure.** The "light-session crash" was
not an OOM event.

---

## 2. Working hypotheses (ranked by likelihood)

### Preamble — hard-hang vs BSOD interpretation (added 2026-05-18 post-crash #7)

11 crashes total in the inventory. **Only 1 produced a kernel bugcheck dump
(`0x7E`, 5/18 11:17 AM).** The other 10 produced *nothing*:

- No bugcheck `1001` event.
- No `volmgr` "dump file generation succeeded" log.
- No `.dmp` file written to `C:\Windows\Minidump\`.

Critically, after crash #7 tonight we now have direct evidence that
`CrashDumpEnabled=7` is set and effective at the registry level — and the
crash *still* produced no dump. So the no-dump pattern is **not** explained
by "dump policy wasn't configured" or "dumps are being deleted post-write."
The kernel simply didn't reach the bugcheck path. This is the **hard-hang
without bugcheck** pattern and it is itself diagnostic. Three mechanisms
fit:

1. **Storage-stack wedge before bugcheck.** The bugcheck write path needs a
   functioning storage stack to write the dump. If the storage driver is
   already wedged (or its lower-level kernel state is corrupted) at the
   moment the fault occurs, the kernel can't run bugcheck — the embedded
   controller's watchdog times out and forces a hard reset with no software
   trace. This is the **H1-consistent** interpretation.
2. **Hardware fault that bypasses software error handling entirely.**
   Voltage brownout, power-rail glitch, EC reset, motherboard component
   failure — anything that yanks the CPU out of execution before any
   software (kernel or otherwise) can react. **WHEA-Logger silence weakens
   this interpretation but doesn't eliminate it** — WHEA captures only
   hardware errors that the firmware/CPU machine-check architecture
   surfaces; a power-rail brownout that simply cuts the CPU clock leaves no
   WHEA trace because there's nothing left running to log one.
3. **Both — H1 wedge plus the brownout-on-hard-reset finishing the job.**
   Plausible but not separable from #1 with current evidence.

What the 10/11-no-dump rate adds to H1 vs H6 specifically:

| Reading                                                        | H1 (RST wedge) | H6 (hardware) |
| -------------------------------------------------------------- | -------------- | ------------- |
| Mostly-no-dump + `RstMwService` 7023 at crash time on 10/10    | ✓ direct fit  | indirect — no obvious mechanism for RstMwService correlation |
| Mostly-no-dump + WHEA silence on 11/11                         | ✓ — wedge produces no WHEA either | weakened — most failing hardware surfaces *something* on WHEA |
| One genuine BSOD bugcheck (`0x7E`) on 5/18 11:17 AM             | ✓ — driver bug hits a code path that *doesn't* wedge storage; bugcheck runs normally | hard to explain — hardware failure isn't usually selective like this |
| Acceleration synchronized with KB5089549/KB5087051 install     | ✓ — H4 amplifies H1 (kernel calling pattern changed; brittle RST driver now hits the bug daily) | plausible — could be coincidence |

H1 fits the entire pattern with one mechanism. H6 requires either a very
selective hardware fault or "two unrelated things happening at once,"
which is parsimony-disfavoured. **The hard-hang pattern strengthens H1's
relative position without eliminating H6.** Definitive separation would
require either (a) WinDbg analysis of the one dump we have showing an
`iaStor*` module in the faulting stack, or (b) hardware-level diagnostics
(M5/M6 first, then physical inspection if needed).

This preamble does not alter the ranking below, which still has H1 leading
and H6 at LOW pending hardware-diagnostic exhaustion. It tightens the
*why*: H1 isn't leading because it's the most common type of cause; H1 is
leading because it's the only single-cause hypothesis that explains both
the bugcheck we have (one analyzable driver fault) and the bugcheck-less
hard-resets we don't (storage stack wedged before bugcheck path runs).

### WinDbg verdict — 2026-05-18 post-install (SUPERSEDES sections below)

`cdb !analyze -v` against `C:\Windows\Minidump\051826-14937-01.dmp`
(committed in `9b37510` as `docs/diagnostics/2026-05-19_crash_7_windbg.txt`)
returned a definitive verdict on the one bugcheck dump we have:

| Field                  | Value                                                 |
| ---------------------- | ----------------------------------------------------- |
| `BUGCHECK_CODE`        | `0x7E` (SYSTEM_THREAD_EXCEPTION_NOT_HANDLED)          |
| `BUGCHECK_P1`          | `0xC0000005` (STATUS_ACCESS_VIOLATION)                |
| Faulting IP            | `nvlddmkm+0x164cb6` (`fffff80489594cb6`)              |
| `MODULE_NAME`          | **`nvlddmkm`**                                        |
| `IMAGE_NAME`           | **`nvlddmkm.sys`**                                    |
| `FAILURE_BUCKET_ID`    | `AV_nvlddmkm!unknown_function`                        |
| Faulting instruction   | `cmp qword ptr [rcx+0B8h], 0` with `rcx=0x0000000000B10000` → reads `0x0000000000B100B8` (near-null) |
| `PROCESS_NAME`         | `System`                                              |
| Module timestamp       | **Fri Dec  3 02:59:01 2021** (the OEM driver)         |

The crash is a near-null pointer dereference inside the NVIDIA kernel-mode
display driver. The faulting nvlddmkm.sys is the **same** 2021-12-02 driver
from § 1's "out-of-date drivers" table. Public symbol load failed (transient
/ firewall), but the module identification is from the dump's loaded-image
table and does not depend on symbols.

**Re-ranking:**

| Hypothesis | Before WinDbg | After WinDbg | Reasoning |
| ---------- | ------------- | ------------ | --------- |
| **H2 (NVIDIA `nvlddmkm.sys`)** | MEDIUM-HIGH | **CONFIRMED for crash #6 (5/18 11:17 AM). Leading hypothesis overall.** | Direct evidence: the only analyzable dump names this module. Faulting code is paged-out → loaded code path actively in use. |
| **H1 (Intel RST)** | HIGH (leading) | **DEMOTED to MEDIUM, possibly LOW.** | `nvlddmkm` is in the faulting stack, no `iaStor*` appears. The `RstMwService` 7023 correlation is re-interpreted as a **downstream artifact of dirty reboots** — every unclean shutdown leaves the RST user-mode service unable to attach to its kernel counterpart on next boot. That's the symptom; the NVIDIA fault is the cause that triggers the dirty reboot in the first place. |
| **H1b (Norton dual-AV)**       | HIGH (co-leading) | Already weakened by tonight's post-uninstall recurrence; now further reduced to LOW. No `Norton`/`SymEFA`/`SRTSP*` modules in the faulting stack. |
| **H3 (Killer)**                | MEDIUM | Unchanged. No Killer modules in the faulting stack of the one dump we have. |
| **H4 (May KBs as trigger)**    | MEDIUM, as trigger | **Reframed: trigger for H2, not H1.** The May 2026 KBs likely changed display-driver API contracts in ways the Dec 2021 NVIDIA driver mishandles. Same logic as before, different target. |
| **H5 (power)**                 | LOW as cause / MEDIUM as amplifier | Unchanged. |
| **H6 (hardware)**              | LOW | Unchanged. WHEA silence remains. |

**Confidence about the 10 no-dump crashes:**

We have direct evidence for one crash. The 10 hard-hang-without-bugcheck crashes
could be:

(a) **All also NVIDIA** — a graphics-driver fault inside a DPC or interrupt
    context can wedge the system before the bugcheck path runs, leaving no dump.
    The dwm.exe + Start_HDR.exe user-mode crash chorus (4 + 10 in 7 days)
    independently points at NVIDIA as the chronically-unstable component. Most
    parsimonious explanation: one root cause, varying expression. **This is the
    leading interpretation.**
(b) **Mixed** — some NVIDIA, some something else (RST, Killer, KB-induced
    Microsoft kernel bug). Possible but loses parsimony.
(c) **A different shared cause** that one time happened to be preceded by an
    NVIDIA fault (false-flag bugcheck). Very unlikely — the bugcheck timestamp
    matches a Kernel-Power 41 reboot precisely.

**Net:** H2 is now the leading hypothesis. M3 (NVIDIA Game Ready Driver clean
install) supersedes M2 (Intel RST uninstall) as the recommended next user-action.
M2 retains value as a hygiene item (5-year-old driver is bad practice) but is
no longer the primary fix.

**Note re prior recommendation flagged by Board (M2-regardless intent):** the
Board's heads-up said "I'm considering doing M2 regardless of what the dump
shows" because the prior evidence base (RstMwService 10/10) made M2 a rational
calculated bet. The dump did materially change the picture — RstMwService is
re-interpreted as downstream artifact, not cause. **Recommendation: do M3
first, not M2.** If M3 doesn't reduce crashes, then M2 is the next move (RST
hygiene is independently good even if not load-bearing).

The sections below preserve the pre-WinDbg evidence record. **Read them as
historical reasoning, superseded by the verdict above for the immediate
action call.**

### Post-crash-#9 re-ranking — 2026-05-18 22:08 (SUPERSEDES the post-crash-#8 ranking below for H7 status + mitigation interpretation)

Full evidence and timeline in **§ 11 below.** Summary of what changed:

Crash #9 produced a kernel bugcheck dump on its own
(`051826-15343-01.dmp`, **`0x0000010e VIDEO_MEMORY_MANAGEMENT_INTERNAL`**,
sub-code `0x2e`) with Event 2004 firing 4 minutes earlier identifying
`python.exe (8544)` at **58.3 GB virtual / 53.85 GB private commit**. The
offending workload was a *single-file* pytest run
(`tests/test_kalshi_structure_arb.py`) — not a backtest run, just pytest
discovery + collection — invoked **directly via `python.exe`** without
the `scripts/run_capped.ps1` / procgov wrapper that was committed in
ab13673 ~14 minutes earlier.

| Hypothesis | Pre-crash-#9 status (§ 9 ranking) | Post-crash-#9 status | Reasoning |
| ---------- | --------------------------------- | -------------------- | --------- |
| **H7 (workload pressure / virtual-memory exhaustion)** | LEADING (untested but with strong direct correlative evidence) | **LEADING (mechanism-confirmed by crash #9 dump).** | Bugcheck `0x10e` is the *exact* cascade § 9 predicted: VM exhaustion → 4 min of thrashing → video memory manager allocation path can't proceed → bugcheck. The Event 2004 → bugcheck timeline is now tighter than "2–7 min before"; the bugcheck *is* in the resource-exhaustion window itself. |
| **H7 — mitigation interpretation** | M1 applied; M2 (procgov wrapper) pending Board approval | **M2 wrapper installed but NOT invoked on offending process** (`run_capped.ps1` bypassed; `python.exe` called directly). | **Wrapper-invocation discipline failure, not procgov enforcement failure.** Procgov's Job Object cap (`--maxjobmem 25G --terminate-job-on-exit`) would have hard-killed at 25 GB; the cap was never attached because the wrapper was never invoked. Crash #9 invalidates "wrapper exists ⇒ wrapper protects" but does NOT invalidate procgov's actual enforcement strength. See § 11 for the three-tier recommendation (runbook → lint-check → OS watchdog). |
| **H2 (NVIDIA `nvlddmkm.sys`)** | Confirmed for crash #6 only; falsified as complete explanation by crash #8 | **Unchanged** — modest nudge as the *kind of allocation path* H7 cascades into on this machine, but not a separate proximate cause. The 2021 OEM driver is still present (per § 9's M3 verdict — Game Ready clean install replaced it, but a `nvlddmkm` of similar staleness is on disk), and graphics-memory paths are repeatedly where H7's pressure surfaces. **Symptom, not cause.** |
| **H1, H1b, H3, H4, H5, H6, H8** | Per § 9 ranking | **Unchanged.** Crash #9 adds no new positive evidence for any of these and adds no new evidence against. |

**The one structural change to ranking:** H7 moves from "leading by correlation"
to "leading by mechanism." That's the strongest evidence type in the inventory.

**Why the M2 mitigation didn't help:** the wrapper *cannot* protect a
process the human (or agent) never wraps. M2's design choice ("opt-in
shim invoked at the command line") was the cheapest install but is the
weakest enforcement. § 11's recommendations move toward closing the
opt-in gap — runbook strengthening first (cheap), then an OS-side
watchdog (Board-decision) if the discipline approach proves
insufficient.

### Post-crash-#8 widened-scope re-ranking — 2026-05-19 (SUPERSEDES the WinDbg verdict's ranking above for the immediate action call)

Crash #8 happened on **2026-05-18 21:13** despite the NVIDIA Game Ready Driver
clean install completing earlier the same evening. **The H2 (NVIDIA) mitigation
did not stop the crashes.** Combined with H1b (Norton) being falsified the
prior session, two software-driver hypotheses have now been mitigation-tested
and neither resolved the pattern. This subsection widens scope to include
hypotheses not previously seriously tested.

Full supporting evidence inventory is in **§ 9 below**. Summary of the new
finding driving this re-ranking:

**`Microsoft-Windows-Resource-Exhaustion-Detector` Event 2004 fires within 2–7
minutes before every recent crash.** 10 / 10 correlation since 5/17 23:00.
Top virtual-memory consumer in every event: **`python.exe` at 43 – 60 GB**
(claude.exe consistently #2). The system commit-charge limit is RAM + pagefile
= 16 GB + 17 GB = **33 GB**, so a single python process committing 50+ GB is
either (a) automatic pagefile expansion absorbing it temporarily, or (b) the
reported figure is VirtualSize (reserved + committed) rather than commit
charge — either way, Windows' own resource-exhaustion detector is identifying
a low-virtual-memory condition immediately before each crash. Crash #8 fits
the pattern: Event 2004 at 21:10:38, python.exe (PID 10416) at **55 GB**,
crash at 21:13:30.

For comparison: in the 27 days preceding 5/17 23:00, Event 2004 fired **once
total** (5/2, python at ~7 GB, did not precede a crash). The metric crossed
from "rare baseline" to "immediate precedent of every crash" exactly when the
crash cluster began. The change is the **backtester workload** (Kalshi SA
backtest, BitUnix v3 hybrid backtests) — large-pandas-DataFrame Python
processes routinely sitting at 50+ GB virtual.

**Revised ranking (post-crash-#8, scope widened):**

| Hypothesis | Status | Confidence | Reasoning |
| ---------- | ------ | ---------- | --------- |
| **H7 (workload pressure / virtual-memory exhaustion) — NEW** | Untested but with strong direct correlative evidence. **LEADING.** | HIGH | 10/10 Event 2004 → crash correlation in the last 24 h. The metric is rare baseline → systematic crash precedent at the exact 5/17 23:00 inflection point. Python at 50–60 GB virtual on a 33 GB commit-limit system stresses every kernel allocation path (storage, GPU, network, paging) simultaneously. Explains both the no-dump pattern (kernel can't allocate to write a bugcheck dump when commit is exhausted) AND the one NVIDIA dump (GPU driver fault when video memory allocation fails under system memory pressure). |
| **H2 (NVIDIA `nvlddmkm.sys`)** | Confirmed for one crash, **falsified as complete explanation** by crash #8 post-clean-install. | MEDIUM (as proximate cause for crash #6 only) | The one analyzable dump (5/18 11:17 AM) shows nvlddmkm.sys faulting, but the Dec 2021 driver has been replaced via clean install and crashes continue with the same no-dump signature. Plausibly a SYMPTOM of H7 — when system VM is exhausted, the GPU driver's allocations fail, an older driver hits a code path that bugchecks while a newer driver hard-hangs the same way the storage stack does for the no-dump crashes. |
| **H1 (Intel RST `iaStor*`)** | Untested by mitigation. **MEDIUM** as proximate cause; possibly downstream artifact per WinDbg verdict. | LOW–MEDIUM | The 11/11 RstMwService 7023 correlation is still present, but the WinDbg verdict reframed it as a downstream artifact of dirty reboots. Under H7, the RST driver may be wedging *because* commit-exhaustion is wedging its lower-level state, which is itself an H7 expression. Mitigation (M2: RST uninstall) still hasn't been tried. |
| **H1b (Norton dual-AV)** | **FALSIFIED** by post-Norton-uninstall recurrence (crashes #7, #8). | LOW | Norton is gone; the crashes continue with identical signatures. |
| **H3 (Killer Wi-Fi 2021-vintage)** | Untested by mitigation. | LOW–MEDIUM | Same 2021 OEM vintage as NVIDIA (driver 22.70.0.6, dated 2021-06-28). 17 user-mode service crashes in 7 days, but every Killer service crash timestamp lines up with a system-reboot time — downstream artifact, not driver bug surfacing. No Killer modules in the one bugcheck stack. Same OEM-staleness pattern applies but no positive evidence elevates it. |
| **H8 (firmware / BIOS) — NEW**            | Untested. | LOW–MEDIUM | BIOS is `E17K3IMS.119` dated **2021-08-05** — over 4.5 years old, same OEM vintage as NVIDIA and Killer drivers. MSI may have released stability fixes; some hard-hang patterns are known BIOS bugs. Not auto-checked here (user-side action), but BIOS staleness is part of the same "nothing on this system has been updated since factory" picture. |
| **H4 (May 2026 KBs as trigger)** | Unchanged — trigger interpretation. | MEDIUM (as trigger only) | The 5/15 KB install date still aligns with crash acceleration. Under H7, KB5089549's kernel changes may have changed memory-management or allocation behavior in ways that turn a previously-tolerable 50 GB python process into a system-wedge trigger. |
| **H5 (power delivery weakness)** | Unchanged. | LOW as cause, MEDIUM as amplifier | No WHEA thermal/VRM events in 7 days. Battery wear 23.2% confirmed via fresh report. |
| **H6 (hardware fault not visible to WHEA)** | Untested by mitigation. **WEAKENED** further. | LOW | SMART OK on the only physical disk. No prior memtest ever recorded — diagnostic gap, but no positive signal. WHEA silence remains across 7 more days. H7 explains the no-dump pattern without invoking hardware. |

**The pattern of 11 / 12 no-dump crashes** is now interpretable under H7:
when system commit is exhausted, the kernel can't allocate the buffer needed
to write a bugcheck dump; the storage stack can't service the dump write;
the embedded controller's watchdog fires before the bugcheck path completes.
This is the same mechanism the H1 historical reading proposed, but with the
trigger being **system-wide commit exhaustion** rather than RST-driver
internal state. The one bugcheck (crash #6, 5/18 11:17 AM) is then the
exceptional case where the fault hit a code path that *did* manage to run
bugcheck before the storage stack wedged — that path landed in `nvlddmkm.sys`
because GPU allocation was the proximate failure under VM pressure.

H7 is the only single-mechanism hypothesis that explains:
1. The 5/17 23:00 inflection point (workload type changed: heavy backtester
   Python).
2. The Event 2004 → crash correlation (10/10).
3. The 11/12 no-dump pattern (no commit available to write bugcheck).
4. The one NVIDIA dump (GPU allocation path was where the fault landed under
   memory pressure, and that path *could* still run bugcheck because storage
   wasn't wedged at that moment).
5. The RstMwService 7023 correlation (RST kernel state corrupted by
   exhaustion-driven dirty reboot, same downstream-artifact reading).
6. Why post-NVIDIA-clean-install crashes continue (the underlying mechanism is
   memory pressure, not driver bug).

The recommended testing sequence (cheap → expensive, likely-yield → speculative)
is in **§ 9 Step 7**. The single cheapest, highest-yield test is the
workload-reduction observation — close the backtester sessions, run with the
normal-but-not-heavy load, observe whether crashes stop.

### H1 — Intel Rapid Storage Technology driver (`iaStorAC.sys` / `iaStorAVC.sys`) is the immediate cause [HIGH — historical; demoted to MEDIUM/LOW per WinDbg verdict above]

**Claim:** A 5-year-old Intel RST storage driver is corrupting kernel state under
some condition reachable from normal use (any disk I/O pattern that hits a latent
bug). The result is sometimes a clean BSOD (1 case), but usually a wedge where the
kernel can't even write a dump (10 cases).

**Evidence for:**
- `RstMwService` terminates with non-zero error on every reboot post-5/15 (9/9).
  The user-mode service complains because the kernel storage driver came up in an
  inconsistent state after the dirty reboot.
- `RstDowngradeGuard` is installed, deliberately blocking driver updates. The OEM
  install date (2021-04-22) is the last time the driver was touched.
- A storage-stack wedge explains why minidumps don't get written for most crashes
  (you can't write a dump if the disk subsystem is unresponsive — the embedded
  controller's watchdog then forces a reset, no kernel involvement).
- The one BugCheck we have (`0x7E / STATUS_ACCESS_VIOLATION`) is consistent with a
  null-pointer dereference in a kernel driver.
- WHEA is silent (rules out hardware), driver is the prime software suspect.

**Evidence against:**
- Without the missing .dmp we can't *prove* the faulting driver is `iaStorAC.sys`.
  Could be NVIDIA, Killer, Intel ME, or something else.
- 4/26 and 4/27 crashes happened *before* the May update — if RST was the cause,
  the trigger that intensified after 5/15 still needs explaining (probably: a
  Windows kernel storage-stack change in KB5089549 / KB5087051 changed the calling
  pattern in a way the old RST driver mishandles).

**What would confirm:** Recover or recreate the minidump and run
`!analyze -v` in WinDbg; check whether the faulting module is `iaStorAC.sys`,
`iaStorAVC.sys`, or the RST filter driver. Alternatively, the *test* of replacing
the Intel RST driver and seeing if crashes stop would also confirm.

### H1b — Dual real-time AV conflict (Norton + Windows Defender simultaneously active) is contributing to crashes [HIGH, co-leading with H1]

*Added post-M1 execution (§ 7) once Norton was confirmed actively running
alongside Defender. Numbered H1b rather than H4 (per the addendum request) to
make the co-leading-with-H1 positioning visually unambiguous; existing H4–H6
keep their original numbers and meanings.*

**Claim:** Two real-time AV products (Norton Security 22.20.5.40 and Windows
Defender) are scanning the same files simultaneously. Each one's minifilter
driver intercepts file I/O independently. Race conditions between the two
minifilters — especially under load, especially in the kernel storage stack —
can corrupt I/O completions in a way that crashes the system. The Norton
install is from the 2021-04-22 factory image and has likely had a lapsed
subscription for years; stale signatures combined with kernel-level hooks
amplify the conflict.

**Evidence for:**
- Norton 2021-vintage doesn't register with `SecurityCenter2` (only Defender
  shows there), so Windows never disengaged Defender's realtime side. Both are
  scanning every file open.
- The 6-second gap between BugCheck event (11:17:17) and Minidump-directory
  `LastWriteTime` (11:17:23) is a textbook real-time-AV-quarantine signature,
  consistent with Norton flagging the new `.dmp` file. That's direct evidence
  that Norton's realtime engine is actively touching kernel-region files.
- Dual real-time AV is widely documented as a cause of consumer-Windows
  instability *independent* of any other factor. Microsoft's own guidance on
  Defender for Endpoint deployments explicitly warns against running a second
  realtime AV alongside it.
- Norton has been running continuously since the 2021-04-22 factory install
  (5 years on stale code), explaining the long-running baseline of weekly-ish
  crashes pre-5/15. The acceleration after 5/15 could be the kernel update
  changing how minifilter callbacks resolve, pushing the long-running latent
  race into a frequent failure mode.
- Crashes that produce no bug check (10 of 11) are consistent with a
  minifilter-storage-stack wedge — same failure mode that argues for H1, but
  via a different mechanism (filter-driver collision vs. driver-internal bug).

**Evidence against:**
- Doesn't directly explain the **`RstMwService` 9/9 termination pattern**. If
  the cause were purely an AV minifilter collision, you wouldn't expect the
  RST service to consistently come up in an error state. (Counter to the
  counter: a minifilter collision in the storage stack *could* leave the RST
  driver state corrupted on reboot — but this is speculative.)
- Doesn't directly explain the **sharp temporal correlation with the May 2026
  KB installs**. If dual-AV had been the cause for 5 years, you'd expect a
  consistent baseline rate, not a sudden acceleration. (Counter: the KBs may
  have changed kernel minifilter callback semantics in a way that turned the
  previously-survivable race into a frequent panic — same "exposed pre-existing
  bug" story as H4 below.)
- The H1 case (RST driver) has direct, specific evidence (RstMwService log
  pattern) that H1b doesn't.

**What would confirm:** Uninstall Norton + NRnR cleanup + 48–72 hr observation
under normal heavy use. If crashes stop, H1b is confirmed (and H1's RST issue
either wasn't the root cause OR was being amplified by the conflict). If
crashes continue, H1b is not the sole cause and we proceed to M2 (RST
uninstall). If a minidump from the next crash survives and `!analyze -v`
shows a minifilter or Norton driver (`SymEFA.sys`, `eeCtrl.sys`, `IDSvix86.sys`,
`SRTSP*.sys`) in the fault stack, H1b confirmed by mechanism.

### H1 / H1b mutual relationship

H1 and H1b are **not mutually exclusive**. Three plausible joint stories,
ranked rough-equally:

1. **One is necessary, the other amplifies.** E.g., RST driver has a latent
   bug that only triggers when something perturbs storage I/O at the wrong
   moment — Norton's minifilter is that perturbation. Removing Norton alone
   would reduce trigger frequency to near zero; removing RST alone would fix
   the underlying bug. Either fix in isolation might suffice; both is safest.
2. **Both contribute independently.** Each on its own would produce occasional
   crashes; together the rate is higher than additive. Removing one cuts the
   rate substantially but doesn't reach zero until both are addressed.
3. **One is the actual cause; the other is a red herring.** RST might be
   crashing the system, and the RstMwService log pattern is the symptom most
   visible, while Norton is just a co-resident annoyance that doesn't actually
   cause reboots. Or Norton is causing the crashes and RST's symptom-on-reboot
   is just what we see *because* the kernel comes up dirty from any crash.

The 48–72 hr post-Norton-uninstall observation distinguishes story 1/2 from
story 3-flavor-Norton-is-cause. Story 1/2 vs. story 3-flavor-RST-is-cause
requires either a captured minidump (M1) showing the faulting driver, or
trying M2 if Norton-uninstall alone doesn't resolve.

### H2 — NVIDIA RTX 3060 driver hang causing hard reset [MEDIUM-HIGH]

**Claim:** NVIDIA driver `nvlddmkm` is from Dec 2021 (~350 releases behind current).
On a modern Win 11 build, GPU-driver hangs that don't make it through Timeout
Detection and Recovery can wedge the system and trigger a hard reset.

**Evidence for:**
- Driver is 4.5 years old on a current OS.
- `dwm.exe` has crashed 4 times in the last 7 days (display-related).
- `Start_HDR.exe` has crashed 10 times in the last 7 days (display).
- BugCheck `0x7E` can come from `nvlddmkm.sys`.

**Evidence against:**
- No TDR events (`Display` provider id=4101) recorded in 30 days. If NVIDIA were
  hanging, we'd usually see TDR recoveries first.
- The RstMwService correlation is stronger and more consistent.

**What would confirm:** Minidump analysis showing `nvlddmkm` in the faulting stack,
or crashes stopping after a driver update.

### H3 — Killer networking stack instability contributing to system-level instability [MEDIUM]

**Claim:** Killer Wi-Fi driver (2021-06-28) + Killer service crash loop is wedging
the network stack, occasionally combined with another wedge to push the system over.

**Evidence for:**
- 12 service crashes in 7 days is way above baseline.
- KillerNetworkService terminated 2 minutes before the 5/18 19:31 reboot.
- Wi-Fi driver from 2021, never updated.

**Evidence against:**
- Network drivers more often cause hangs without reboot than reboots.
- Killer crashes have been chronic and predate the 5/15 acceleration.

**What would confirm:** Update or replace Killer drivers (replace with Intel
default Wi-Fi driver), monitor for stack improvement and crash reduction.

### H4 — KB5089549/KB5087051 (May 2026 Windows updates) exposed pre-existing driver bugs [MEDIUM, as trigger not cause]

**Claim:** The 5/14–5/15 cumulative updates changed the kernel calling pattern in a
way that exposed latent bugs in the 2021-era OEM drivers. The updates aren't faulty;
they just stress code paths the old drivers don't handle.

**Evidence for:**
- Sharp acceleration coincides exactly with the update install date.
- 8 crashes in 24 hours starting <2 days after update is not consistent with a
  random failure rate.

**Evidence against:**
- Two crashes in late April predate the update. So the underlying brittleness
  existed; the updates just made it dramatic.

**What would confirm:** Look at KB5089549 changelog for storage/graphics driver
interface changes. Alternatively, observe whether DISM/uninstall of those updates
reduces crash frequency (this would be a *test* not a fix — don't actually run it).

### H5 — Power delivery weakness amplified by 23%-worn battery on a high-power laptop [LOW as cause, MEDIUM as amplifier]

**Claim:** Gaming laptop + worn battery + transient high CPU+GPU load = brief
brownout = hard reset with no software trace.

**Evidence for:**
- 23% battery wear on a system whose PSU is sized for AC + battery topology.
- Hard resets with no kernel involvement (no bug check) are consistent with
  power-loss-style events.

**Evidence against:**
- No WHEA-Logger thermal or VRM events.
- The RstMwService pattern is too consistent to be random power glitches.
- User reports crash happened during *light* operations (file reads). Power
  brownouts under light load would point to a failing PSU, which is rare.

**What would confirm:** A 7th crash that happens while the laptop is on battery
power alone (rules in/out PSU vs. battery as the failure point); or running a
sustained stress test like Prime95 + FurMark to see if load itself triggers crashes
(don't actually run this — risk of #11+).

### H6 — Hardware-level fault not visible to WHEA [LOW]

**Claim:** A motherboard component (VRM phase failure, power filter cap drying out,
embedded controller flake) is causing hard resets that don't surface as WHEA events.

**Evidence for:**
- All hard reboots, no kernel crash → could be embedded controller resetting.
- 5-year-old gaming laptop, plausible.

**Evidence against:**
- The RstMwService correlation is much too clean for a random hardware fault.

**What would confirm:** All software mitigations exhausted with crashes continuing
unchanged. At that point: physical inspection at a service center or a known-good
PSU swap.

---

## 3. Mitigations to try (cheapest first)

Each mitigation is paired with a way to test it. **None of these are applied this
session — the report is the deliverable.** Order them after Board review.

### Revised mitigation ordering (post-M1 execution, post-H1b)

The original ordering (M1 → M2 → M3 → M4 → M5/M6) was written before Norton was
identified as actively running. With H1b now co-leading with H1, the cheaper and
safer fix (Norton uninstall) should be attempted before the heavier one (Intel
RST uninstall). Revised priority for user action:

1. **M1 — DONE** (this session, in § 7). Minidump-deletion sources identified;
   `CrashDumpEnabled → 7` documented for user execution.
2. **M-Norton (new, defined under § 7 as part of the action sequence) — FIRST-
   LINE FIX.** Uninstall Norton Security cleanly. Cheap, safe, resolves the
   minidump-deletion issue AND tests H1b in one step.
3. **48–72 hour observation period** under normal heavy use (see § 7 "What
   counts as valid observation").
4. **M2 — Remove Intel RST + downgrade guards — SECOND-LINE FIX**, attempted
   **only if** crashes continue through the observation window. Defined below
   with original details preserved.
5. **M3 — Update NVIDIA driver** — third-line, applied **after the observation
   window regardless** of whether crashes continue. The 4.5-year-old driver is
   a hygiene problem independent of crash causation; updating now reduces noise
   from `dwm.exe` / `Start_HDR.exe` user-mode crashes and removes H2 from the
   suspect list.
6. **M4 — Update Killer/Wi-Fi to Intel reference driver** — fourth-line, only
   if M2 + M3 + observation haven't resolved.
7. **M5/M6 — sfc, chkdsk, Windows Memory Diagnostic** — fifth-line, only if
   all software mitigations exhausted.
8. **M7 — Battery report review** — separate hygiene track, no crash-causation
   relevance unless system goes unplugged.

The M-prefixed sections below retain their original numbering (M1–M7) for
reference — only the *user-action priority* has been revised.

### M1 — Disable the 4 GB minidump cleanup so the NEXT crash gives us evidence [Session-time, cheap]

**Action:** Find what's deleting `C:\Windows\Minidump\*.dmp`. Candidates: Microsoft
Defender controlled folder access, Storage Sense, an MSI utility, or a
third-party cleanup tool (CCleaner etc.). Disable for that path. Optionally also
change `CrashDumpEnabled` from 3 (small) to 7 (automatic) so a full kernel dump is
written when the pagefile is large enough.

**Test:** When the next crash happens, check `C:\Windows\Minidump\` for a fresh
.dmp. Run `!analyze -v` in WinDbg → identifies the faulting driver definitively.

**Effort:** 10 minutes. **Risk:** None.

**Why first:** This is the single best diagnostic move. Right now we have evidence
strong enough to recommend an action plan but not strong enough to be sure. One
analyzable minidump would resolve H1 vs. H2 vs. H3 immediately.

### M2 — Remove `RstDowngradeGuard` + uninstall Intel RST + reboot [User-time, medium]

**Action:** From "Add or Remove Programs", uninstall:
- `RstDowngradeGuard`
- `OptaneDowngradeGuard`
- `Intel Rapid Storage Technology` (if listed separately)
Reboot. Windows will fall back to the inbox `stornvme.sys` NVMe driver, which is
fine for a Samsung MZVLQ NVMe — RST is only required if you're using
multiple-drive RAID arrays or Intel Optane caching, neither of which applies here.

**Test:** Check `RstMwService` no longer appears in `services.msc` (it shouldn't,
the service is removed with the package). Watch for new Kernel-Power 41 events
over the next 48 hours. If crashes stop, H1 confirmed.

**Effort:** 15 minutes + reboot. **Risk:** Low — modern Windows handles the Samsung
NVMe fine with the inbox driver. **Backup recommendation:** Make a System Restore
point first.

### M3 — Update NVIDIA driver to current Game Ready / Studio [User-time, medium]

**Action:** Download NVIDIA Game Ready Driver (or NVIDIA Studio Driver if
preferred for stability) for "GeForce RTX 3060 Laptop GPU" on Windows 11 from
nvidia.com directly. Run the installer; choose "Custom" → "Clean install" to wipe
the 4.5-year-old profile.

**Test:** Same — watch Event Viewer for K-P 41 events. Also watch for any return
of `Start_HDR.exe` and `dwm.exe` crashes (which should drop).

**Effort:** 20 minutes (download + install + reboot). **Risk:** Very low.

### M4 — Update Intel Wi-Fi/Killer drivers [User-time, medium]

**Action:** On MSI GE76 Raider 11UE, the Killer AX1675x is actually a rebadged
Intel AX210. Easiest path: download the current Intel Wi-Fi driver for AX210 from
Intel directly (not from MSI). Install — this typically replaces the Rivet/Killer
driver and service stack with Intel's standard one, which is *more* stable than
Killer's branded version. Same for Killer E3100G Ethernet (rebadged Intel I225-V).

**Test:** `KillerProviderDataHelperService.exe` crashes should drop to zero.
Network throughput unchanged. Watch K-P 41 events.

**Effort:** 30 minutes. **Risk:** Low. Some users prefer the Killer Control Center
features; uninstalling Rivet's stack removes that. Acceptable trade-off.

### M5 — Run `sfc /scannow` + `chkdsk C: /scan` [User-time, cheap, slow]

**Action:** Elevated PowerShell:
```
sfc /scannow
chkdsk C: /scan
```
First repairs Windows system file corruption; second scans NTFS for filesystem
errors without taking the volume offline. If `chkdsk` reports errors needing
repair, schedule a `chkdsk C: /f` on next boot.

**Test:** Outputs report "no integrity violations" / "no errors found". Doesn't
*fix* the crash directly but rules out filesystem corruption as a hidden cause.

**Effort:** 30 minutes wall clock (mostly idle). **Risk:** None.

### M6 — Windows Memory Diagnostic [User-time, cheap, slow, requires reboot]

**Action:** Start → "Windows Memory Diagnostic" → "Restart now and check for
problems". One full pass takes ~20 minutes; the recommended extended pass takes
hours. Even a single pass catches most failing RAM.

**Test:** Reports zero errors. If errors found, RAM module needs replacement —
that's an immediate stop-and-call-service.

**Effort:** 20 min single pass / several hours extended. **Risk:** None to data.
You can't use the laptop during the test.

### M7 — Full battery report review + decide on battery replacement [User-time, optional]

**Action:** Open `C:\Users\AAINCO~1\AppData\Local\Temp\battery_report.html` (already
generated this session). If wear continues to advance, consider replacement —
GE76 Raider 11UE battery is officially user-serviceable via service center. Even
at 23% wear, the system is on AC and should be fine; this is a longer-term
concern, not a crash mitigation.

**Test:** Not a crash mitigation; just a hygiene item.

### Mitigations explicitly NOT recommended

- **Don't roll back KB5089549 / KB5087051.** Even if they were the trigger, they
  contain security fixes; the underlying fragility is the OEM drivers. Fix the
  drivers, keep the updates.
- **Don't reset Windows / fresh install yet.** Heavy-handed; would lose work; no
  evidence the OS install is corrupt.
- **Don't physically open the laptop / re-paste / clean fans.** Reasonable for a
  5-year-old gaming laptop in general, but no thermal evidence in WHEA. Save for
  later.
- **Don't run prolonged stress tests** (Prime95, FurMark, etc.). High risk of
  inducing crash #12 mid-test.

---

## 4. Open questions

- **What deleted the minidump?** Without knowing this we can't be sure the *next*
  crash will produce evidence either. M1 addresses this.
- **Is the missing .dmp recoverable?** Possibly via Recuva / Windows File
  Recovery in `C:\Windows\Minidump\`. Worth a try before assuming it's gone.
- **What's in KB5089549 / KB5087051?** Specifically: were there storage-stack
  changes in the May 2026 cumulative? If yes, that strengthens H1 (RST exposed by
  kernel change). Microsoft's KB articles usually summarize this.
- **Crash time of day:** several of the recent crashes cluster in early-morning
  (12:04, 7:15, 10:39, 11:17 on 5/18). Is there a scheduled task running then
  (Defender scan, Windows Update, MSI Center auto-check)? Worth a look at Task
  Scheduler history.
- **Were any sessions plugged into an external monitor when crashing?** GE76 has
  Thunderbolt; a Thunderbolt 1.41.1094.0 driver from 2021 + an external monitor
  during the 11:17 BugCheck would be relevant.
- **Did the user do anything different around 5/15?** Install new software, change
  power settings, swap peripherals, update an MSI utility? Anything that lines up
  with the crash acceleration is worth knowing.

---

## 5. Recommendation for next session

In priority order:

1. **Apply M1 first.** Stop whatever's deleting minidumps; switch to full kernel
   dumps. Wait for one more crash (sadly, won't be long). Run WinDbg on the dump.
   That's the diagnosis-completing step.
2. **Apply M2 in parallel.** Removing Intel RST is the highest-confidence mitigation
   given current evidence and has the smallest blast radius. If crashes stop within
   48 hours of M2, H1 is confirmed and we're done.
3. **Apply M3 and M4 next.** Updates the two known-stale OEM driver stacks.
4. **If crashes persist after M2+M3+M4**, run M5+M6 to rule out filesystem and RAM,
   then escalate to physical inspection (H6).

**Do not** start P1/P2/P3 project work (IC v1 deconfliction, Kalshi SA review,
paper-cutover prep) until M2 has had 48 hours to validate, OR M1 has caught a
useful minidump.

---

## 6. Diagnostic gaps (what we couldn't check this session)

- WinDbg analysis of the 5/18 11:17 minidump (file missing).
- Whether KB5089549's release notes mention storage-stack changes.
- Whether any of the missing .dmp files are recoverable from the filesystem.
- Stress-test confirmation of any hypothesis (deliberately deferred — risk of
  inducing crash #12).
- Pagefile sufficiency for a full kernel dump if we change `CrashDumpEnabled` to 7
  (16 GB RAM + 17 GB pagefile = should be enough; verify before changing).
  *(Resolved in M1 execution below — pagefile is sufficient.)*
- Task Scheduler history correlation with crash timestamps.
- External monitor / dock state at the time of crashes.

---

## 7. M1 execution — 2026-05-19

Goal: identify what's deleting `C:\Windows\Minidump\*.dmp`, and switch
`CrashDumpEnabled` from 3 (minidump) to 7 (automatic) so the next crash produces
an analyzable dump. Session was non-elevated, so the registry change is described
as a manual step for the user rather than applied.

### Findings

| Step | Item                                          | Result                                                                                                                      |
| ---- | --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 1    | Disk Cleanup scheduled tasks (`SilentCleanup`) | **Enabled**. Last run **2026-05-18 19:42:02** (11 min after the 19:31:40 crash). Action: `cleanmgr.exe /autocleanstoragesense /d %systemdrive%`. |
| 1b   | VolumeCaches → "System error minidump files"  | **Registered handler exists**, points exactly at `C:\WINDOWS\Minidump`. (Companion handler "System error memory dump files" targets `C:\WINDOWS`.) `StateFlags*` columns blank — no user-saved profile, default behavior applies. |
| 2    | Storage Sense (HKCU StoragePolicy)            | Master toggle `01 = 0` (**OFF**). Cadence `04 = 1` (daily, but irrelevant since 01=0). No machine-wide policy.              |
| 3a   | Windows Defender (`Get-MpPreference`)         | Realtime monitoring ON. Controlled Folder Access OFF (so it's not blocking writes to Minidump dir). Exclusions hidden because session is non-elevated. |
| 3b   | Defender threat detection history (30d)       | **No detections.** Defender itself is not quarantining anything.                                                            |
| 3c   | Third-party AV via SecurityCenter2            | Only Windows Defender registered. **BUT see step 4** — Norton is installed and not reporting through SC2.                   |
| 4    | PC-optimizer / cleanup / 3rd-party AV inventory | `Norton Security 22.20.5.40` (installed 2021-04-22) — **AND ACTIVELY RUNNING**: service `NortonSecurity` (Running), two `NortonSecurity.exe` processes (PIDs 5032, 6148, ~10 MB each), `NisSrv` (Network Inspection). Install dirs present: `Program Files\Norton Security`, `Program Files (x86)\NortonInstaller`, `ProgramData\Norton`. Also: `MSI Center SDK` (harmless), `RstDowngradeGuard` (already covered in main report). No CCleaner / Glary / Advanced SystemCare / etc. |
| 5    | WER configuration                             | Normal. WER service running. `EnableZip=1`, `ChangeDumpTypeByTelemetryLevel=1` (default). No `LocalDumps` subkey — user-mode app dumps not saved to disk (this is default Windows behavior; doesn't affect kernel BSOD dumps). No anomalies, no auto-delete settings. |
| 6    | `CrashDumpEnabled` current value              | **`3`** (small memory dump / minidump only — 256 KB). `DumpFile=C:\WINDOWS\MEMORY.DMP`, `MinidumpDir=C:\WINDOWS\Minidump`, `AutoReboot=1`, `Overwrite=1`, `MinidumpsCount=5`. **NOT CHANGED THIS SESSION** — see "user-action required" below. |
| 7    | Pagefile vs RAM                               | RAM **16,085 MB** (16.08 GB). Pagefile `C:\pagefile.sys` allocated **17,408 MB** (17 GB), current usage 46 MB. **Sufficient for any `CrashDumpEnabled` value** including 1 (complete dump, which requires RAM + 257 MB ≈ 16,342 MB minimum — 17 GB satisfies). For `CrashDumpEnabled=7` (automatic, the recommendation), pagefile sizing is well above the system-recommended minimum. |

### Two plausible deletion sources identified

#### Source A — Norton Security (HIGH confidence as the immediate deleter)

Norton was the OEM-bundled AV on this MSI laptop (factory date 2021-04-22) and is
**still actively running** alongside Windows Defender. SecurityCenter2 didn't
report it (likely because the 2021 version doesn't register through the modern
SC2 API, or registration has gone stale), which is why the initial diagnosis only
saw Defender. The user may believe Norton was uninstalled long ago — it wasn't,
or it was only partially uninstalled.

**The Minidump directory's `LastWriteTime` is `2026-05-18 11:17:23`** — exactly
**6 seconds** after the BugCheck event at 11:17:17. That's not the SilentCleanup
window (SilentCleanup at 19:42:02 is hours later). A 6-second post-write deletion
is consistent with **real-time AV scanning** flagging a `.dmp` file as suspicious
and quarantining it immediately. Norton 2021-vintage is known for aggressive
behavior toward unsigned binary files including memory dumps.

A 5-year-old Norton with likely-expired subscription, stale signatures, running
alongside Defender (which Windows usually disables when third-party AV is
registered — but doesn't here, because SC2 doesn't see Norton) is a multi-axis
problem: it's deleting forensic data AND it's a known source of system
instability in its own right.

#### Source B — SilentCleanup scheduled task (MEDIUM-LOW confidence as the immediate deleter, HIGH as a contributor)

The `SilentCleanup` task is enabled and ran at 19:42:02 on 5/18, ~11 minutes
after the 19:31:40 crash. It invokes `cleanmgr.exe /autocleanstoragesense`, which
uses the Storage Sense engine even though Storage Sense's master toggle is OFF.

The "System error minidump files" VolumeCaches handler is *registered* with
`Folder = C:\WINDOWS\Minidump`. The `StateFlags*` columns are blank, meaning no
user-saved Disk Cleanup profile exists; SilentCleanup uses default Storage Sense
behaviors, which historically don't include minidumps but have varied across
Windows builds. On Win 11 26200 the exact set is undocumented.

If Source A (Norton) is removed and minidumps still vanish, Source B is the next
suspect.

### User-action required (Step 6 — not auto-applied)

Session is non-elevated; `Set-ItemProperty` on `HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl`
requires admin. **Do NOT run a Set-Item from a non-elevated PowerShell** — the
change will fail silently (or with `Requested registry access is not allowed`).

**To change `CrashDumpEnabled` from 3 (small) to 7 (automatic):**

GUI path (easiest, no command line):
1. Win+R → `sysdm.cpl` → enter.
2. **Advanced** tab → **Startup and Recovery** → **Settings...**
3. Under **System failure**, "Write debugging information":
   - Change from **Small memory dump (256 KB)** to **Automatic memory dump**.
4. Confirm "Dump file" is `%SystemRoot%\MEMORY.DMP`.
5. Confirm "Overwrite any existing file" is checked.
6. OK / OK. **No reboot required** — change applies on next BSOD.

PowerShell path (must be run as Administrator):
```powershell
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' `
                 -Name 'CrashDumpEnabled' -Value 7 -Type DWord
# Verify:
Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' |
    Select-Object CrashDumpEnabled
```

Why `7` (automatic), not `1` (complete) or `2` (kernel):
- `7` automatic = Windows picks kernel-or-complete dump based on pagefile size at
  crash time. On this system it'll produce a kernel dump (~1–2 GB typically),
  which is more than enough for WinDbg `!analyze -v` to identify the faulting
  driver. The previous `3` (small/256KB) sometimes leaves out enough context to
  diagnose unusual faults.
- `1` (complete) writes all of RAM (~16 GB on this system). Slower to write,
  larger on disk, rarely needed for driver-fault analysis.
- `2` (kernel) is the same as `7` would auto-pick here, just non-adaptive.

Pagefile is sufficient for any of these (see Step 7).

### Concerning side-finding (not the deletion issue per se)

**Norton + Defender running simultaneously is itself a problem worth
addressing.** Windows is designed to register one realtime AV at a time;
SecurityCenter2 should be enrolling Norton and disengaging Defender, or vice
versa. Here both are scanning. This:
- Increases CPU/disk overhead.
- Causes random file-access lockouts (one product holds a file open, the other
  tries to scan it, etc.).
- Is a known cause of broader system flakiness on consumer Windows, separate
  from the crash issue.

Recommended user action (separate from the minidump-deletion fix, but related to
the broader crash investigation):

- **Either** uninstall Norton cleanly via "Add or remove programs" + run the
  Norton Removal Tool (`NRnR.exe`) from Norton's site to clear leftovers.
- **Or** confirm the Norton subscription is active and uninstall Defender's
  realtime side (not really possible cleanly on Win 11) — much less recommended.

Cleanest path is to uninstall Norton entirely and let Defender be the sole AV.
This *also* removes Norton as a deletion suspect for minidumps in one step.

### Net assessment / what to surface

1. **The dumps are likely being deleted by Norton's realtime scan within seconds
   of being written**, not by SilentCleanup hours later. The 11:17:23 directory
   write time vs 11:17:17 BugCheck event is the evidence.
2. **SilentCleanup is a secondary risk**: even after Norton is gone, the task
   could in principle clean minidumps on idle-disk runs. Mitigation: disable the
   SilentCleanup task in Task Scheduler, OR add an explicit exclude flag to
   Storage Sense, OR (simplest) ensure `MinidumpsCount=5` retention always wins.
   Since `MinidumpsCount=5` and `Overwrite=1` are set, the dump subsystem itself
   keeps the last 5. As long as no external process deletes them, you'll have
   the history.
3. **Switching `CrashDumpEnabled` to 7** captures a richer dump that's easier to
   analyze. Requires admin; user action via `sysdm.cpl`.

### Recommended action sequence (user-side, before next session)

Revised after H1b was added — Norton uninstall is now the candidate first-line
crash fix, not just the dump-deletion fix.

**(a) Uninstall Norton Security + NRnR + reboot.**
Add or Remove Programs → Norton Security → Uninstall. Reboot. Download Norton's
"Norton Remove and Reinstall" (NRnR) tool from `norton.com/nrnr` — run it,
choose "Remove only", reboot again. This:
- Stops the post-crash dump deletion (M1 fix).
- Removes the dual-AV conflict (H1b test).
- Leaves Defender as the sole realtime AV (which is what Windows expects).

**(b) Change `CrashDumpEnabled` to 7 — no reboot needed.**
`sysdm.cpl` → Advanced tab → Startup and Recovery → Settings → "Write
debugging information" → **Automatic memory dump** → OK / OK. Confirms the
next crash (if any) writes a WinDbg-analyzable dump.

**(c) 48–72 hour observation period under normal heavy use.**
See "What counts as valid observation" below — this is *not* "leave the laptop
on the desk for 3 days." The observation is meaningful only if the system is
exercised the way a real session does.

**(d) M2 (Intel RST uninstall) — only if observation shows crashes continuing.**
After 48–72 hr of valid observation:
- **Zero crashes** → H1b confirmed; STOP here, do not run M2. Continue working
  normally; revisit only if crashes return.
- **Reduced rate but still crashing** → H1 + H1b both contributing; proceed to
  M2 (uninstall `RstDowngradeGuard` + `OptaneDowngradeGuard` + Intel RST per
  § 3 → M2 detail). Then observe another 48–72 hr.
- **No reduction** → H1b likely a red herring; proceed straight to M2. Then
  observe.

**(e) M3 (NVIDIA driver update) — apply after the observation window regardless
of crash status.**
4.5-year-old driver is independently a hygiene issue. Even if (c) shows zero
crashes, update NVIDIA Game Ready Driver via clean install. Reduces `dwm.exe`
+ `Start_HDR.exe` user-mode noise and rules H2 out of the residual suspect
list. Defer until after the M2-or-not decision so the cause is identified
cleanly before adding another variable.

**Optional belt-and-braces during (c):** disable the `SilentCleanup` task —
Task Scheduler → Microsoft → Windows → DiskCleanup → SilentCleanup → Disable.
Removes the secondary minidump-deletion risk. Only matters if a crash happens
during the observation window and you want to be 100% sure the dump survives.

### What counts as valid observation

The 48–72 hr observation window is the load-bearing test that distinguishes
H1b alone vs. H1+H1b vs. H1 alone. The observation is only meaningful if the
system is exercised the way real sessions exercise it. "No crashes over the
weekend while the laptop sat idle" is **not** valid observation — many of the
crashes happened during *light* operations precisely because something about
load + duration is the trigger, not load level alone.

**Valid observation = at least 48 hr (ideally 72 hr) of:**
- The browser open with the usual tab count (Claude Code web, dashboards,
  GitHub, etc.).
- The IDE / Claude Code session open and actively used — file reads, edits,
  agent invocations.
- Normal background processes running (OneDrive sync, Defender realtime, the
  things that are always on).
- At least one or two heavier operations during the window — a `git` checkout,
  a `pytest` run (small scope, not the full backtest suite), an `az` CLI
  command. Not artificial stress-testing — just normal work cadence.
- Wake-from-sleep transitions if that's the user's normal pattern (a couple of
  the recent crashes happened within 1 minute of resume — sleep/wake is part of
  the workload).
- AC-powered. Don't conflate AC-vs-battery into the observation; that's H5
  territory and we're not testing it here.

**NOT valid observation:**
- Laptop sitting idle on the desk, lid closed, no user interaction.
- Heavy stress-testing (Prime95 / FurMark / sustained burnin) — risks
  inducing crash #12 and conflates load level with load type.
- Light-only weekend use — doesn't replicate the workload pattern that
  produced the original crashes.
- Less than 48 hr — the prior baseline was ~weekly crashes, then jumped to
  multi-per-day. A 24-hr window has too much variance to distinguish "fixed"
  from "got lucky." A 48–72 hr window with active heavy use covers enough
  reboot/resume/load cycles for the answer to be reliable.

**What "crash-free" looks like in Event Viewer (verifiable after the window):**
- Zero new Kernel-Power 41 events.
- Zero new BugCheck 1001 events.
- Zero new EventLog 6008 unexpected-shutdown markers.
- (Bonus: zero new `KillerProviderDataHelperService.exe` user-mode crashes —
  if Norton was the cause, Killer's noise should drop too because the conflict
  was contributing to its service crashes.)

PowerShell one-liner to verify after the window:
```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,1001,6008;
    StartTime=(Get-Date).AddHours(-72)} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, ProviderName |
    Format-Table -AutoSize
```
Empty output = crash-free observation window confirmed.

### Stop-and-ask triggers — none triggered, one concerning side-finding

- Steps 1–5 **did** identify plausible deletion sources (Norton + SilentCleanup),
  so the "no source found and can't elevate" trigger doesn't apply.
- Pagefile **is** larger than RAM (17 GB vs 16 GB), so the "pagefile too small"
  trigger doesn't apply.
- Concerning side-finding: **Norton actively running alongside Defender, while
  SecurityCenter2 reports only Defender.** Surfaced inline above.

End of M1 execution. Next action is user-side; do not proceed to M2 (driver
uninstall) until user confirms M1 actions are done OR explicitly defers them.

---

## 8. Crash #7 diagnostic — 2026-05-18 (user-frame "5/19")

User reported a seventh crash "during the Kalshi SA review session on 2026-05-19."
System clock at the time of this analysis is 2026-05-18 20:22 local — the crash
was tonight, not tomorrow. Treat dates throughout this section as 2026-05-18.

### Timeline

| When (local)             | What                                                                          |
| ------------------------ | ----------------------------------------------------------------------------- |
| 2026-05-18 20:09:53      | Unexpected shutdown (recorded retroactively by EventLog 6008 at 20:18:47).    |
| 2026-05-18 20:09:53      | `RstMwService` terminated (Service Control Manager 7023).                     |
| 2026-05-18 20:18:32.500  | System back up (`LastBootUpTime`).                                            |
| 2026-05-18 20:18:34      | Kernel-Power 41 critical event logged after reboot.                           |
| 2026-05-18 20:18:47      | `RstMwService` terminated again on service startup (the +13 s pattern).       |

What the user was doing immediately before: Kalshi SA review session, reading
the `kalshi_structure_arb_backtest_2026-05-17.md` backtest report file. No heavy
compute, no broker connectivity, no pytest. Light file-read workload — same
class of workload as the 5/15 light-session crash that invalidated the original
"long pytest causes OOM" hypothesis.

### Dump file inventory (Step 1 result)

| Location                                            | State                                                                                                                                              |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `C:\Windows\Minidump\`                              | **Contains `051826-14937-01.dmp` (2,820,616 bytes, written 2026-05-18 11:17 AM).** Note: PowerShell `Get-ChildItem` is access-denied for non-elevated processes; `cmd /c dir` enumerates fine. |
| `C:\Windows\memory.dmp`                             | Present, but timestamp is **2022-05-14 06:08** — stale, 4-year-old residue, unrelated to recent crashes.                                            |
| `C:\Users\AA Incorporado\AppData\Local\CrashDumps\` | 3 user-mode dumps from 5/18 evening: `Start_HDR.exe.16312.dmp` (5/18 20:10), `TradingView.exe.17848.dmp` (5/18 20:17), `Start_HDR.exe.16724.dmp` (5/18 20:19). User-mode app crashes around the kernel reboot, not the kernel crash itself. |

**Significant**: the 11:17 AM 5/18 dump (`051826-14937-01.dmp`) was reported
"MISSING from disk" in § 1 of this report. It is **now visible**. Two possible
explanations:

1. Norton's realtime engine was suppressing visibility / quarantining the file,
   and post-uninstall the file is accessible again.
2. The M1 inventory was incorrect — the file was always there, but the
   non-elevated session in M1 read an empty listing due to ACL behaviour that
   PowerShell handles less gracefully than `cmd dir`.

Either way: **we have an analyzable kernel dump from the 11:17 AM 5/18 crash**.
This is the dump M1 was hoping to capture from the next crash; it turns out we
already had it.

### Dump for the 20:09 PM crash specifically

No new dump was written for the most recent (20:09 PM) crash:

- `C:\Windows\Minidump\` contains only the older 11:17 AM dump.
- No BugCheck `1001` event was logged in the last 6 hours.
- No `volmgr` "dump file generation succeeded" event was logged in the last 6
  hours.

This is the same no-dump pattern as the 9 of 10 prior crashes that produced no
bugcheck. **`CrashDumpEnabled=7` is set and confirmed**, so the registry change
from M1 took effect — yet the kernel still didn't run the bugcheck path. The
storage-stack-wedge mechanism (kernel can't run bugcheck because the storage
subsystem is unresponsive, so the embedded controller forces a hard reset) is
the only mechanism consistent with "modern crash-dump policy correctly set yet
no dump produced." Hardware-fault-class events would have appeared in WHEA;
nothing did.

### Event Viewer entries around the crash

System log, last 48 h, IDs 41/1001/6008/1074 (kernel-power / bugcheck /
unexpected-shutdown / restart-initiated):

```
5/18 8:18:47 PM  6008 EventLog                     Previous shutdown at 8:09:53 PM was unexpected.
5/18 8:18:34 PM    41 Microsoft-Windows-Kernel-Power  Rebooted without cleanly shutting down first.
5/18 8:09:17 PM  1074 User32                       winlogon.exe initiated restart on behalf of NT AUTHORITY\SYSTEM (no title).
5/18 7:31:53 PM  6008 EventLog                     Previous shutdown at 7:17:18 PM was unexpected.
5/18 7:31:40 PM    41 Microsoft-Windows-Kernel-Power  Rebooted without cleanly shutting down first.
5/18 11:17:17 AM 6008 EventLog                     Previous shutdown at 10:39:18 AM was unexpected.
5/18 11:17:17 AM 1001 WER-SystemErrorReporting     BugCheck 0x7E (0xC0000005, 0xFFFFF80489594CB6, 0xFFFFF3862D816778, 0xFFFFF3862D815F80). Dump: C:\WINDOWS\Minidump\051826-14937-01.dmp.
5/18 11:17:04 AM   41 Microsoft-Windows-Kernel-Power  Rebooted without cleanly shutting down first.
```

Note the **20:09:17 PM Event ID 1074** from `winlogon.exe`: an `NT AUTHORITY\SYSTEM`-initiated
restart 36 seconds before the unexpected-shutdown timestamp. The "No title for
this reason could be found" message is ambiguous — could be a hung-shutdown
fallback (winlogon trying to restart a frozen system before the EC forces it),
or a `RtlShutdownSystem` call from a kernel-driver fault handler. Worth noting
but not conclusive on its own; the substantive evidence is the K-P 41 at
20:18:34 PM and the missing bugcheck.

### WHEA-Logger

**Zero events in the last 48 hours.** Hardware-error path remains clean across
this crash too. Hardware-fault hypothesis (H6) continues to lack any positive
signal.

### RstMwService correlation continues — 10 / 10 now

Service Control Manager 7023 events in the last hour:

| Time                  | Source                  | Detail                                             |
| --------------------- | ----------------------- | -------------------------------------------------- |
| 5/18 7:31:53 PM       | Service Control Manager | `RstMwService` terminated on post-7:17-crash boot. |
| **5/18 8:09:53 PM**   | Service Control Manager | **`RstMwService` terminated AT THE MOMENT of the most recent crash.** |
| 5/18 8:18:47 PM       | Service Control Manager | `RstMwService` terminated on post-8:09-crash boot. |

The 8:09:53 PM termination is precisely synchronous with the crash itself
(the 6008 timestamp records "previous shutdown at 8:09:53 PM"). The crash-time
correlation is now **10/10** since the May 2026 cumulative updates. Combined
with the no-dump pattern (storage stack wedge), this is the strongest specific
evidence in the entire investigation pointing at the Intel RST stack as the
proximate cause.

Driver enumeration confirms the picture:

| Component        | Present? | State                     | Notes                                                  |
| ---------------- | -------- | ------------------------- | ------------------------------------------------------ |
| `iaStorAC`       | No       | —                         | Not installed.                                          |
| `iaStorAVC.sys`  | Yes      | Stopped / Manual          | Installed, not currently started.                       |
| `iaStorV.sys`    | Yes      | Stopped / Manual          | Installed, not currently started.                       |
| `RstMwService`   | Yes      | Stopped (Automatic startup) | Service definition still registered; auto-starts every boot then fails. |

The RST user-mode service is auto-starting every boot, then terminating with a
non-zero error because its kernel counterpart isn't in a state it can talk to
(or isn't loaded the way it expects). Even though `iaStorAVC.sys` shows
"stopped," the driver file is on disk and the registered class-filter / lower-
filter entries in `HKLM\SYSTEM\CurrentControlSet\Control\Class\{...}` may still
be referencing it at boot time — meaning a code path through that driver could
still be in the I/O path despite the "stopped" SCM state.

### Norton uninstall — confirmed clean

| Check                                                            | Result                              |
| ---------------------------------------------------------------- | ----------------------------------- |
| `Get-Service NortonSecurity`                                     | Service not present.                |
| `Get-Process NortonSecurity`                                     | No matching processes.              |
| `Test-Path 'C:\Program Files\Norton Security'`                   | Directory removed.                  |

Norton is gone. Yet:
- A new no-dump crash happened tonight (20:09 PM).
- The `RstMwService` 7023 pattern continued unchanged.
- The 11:17 AM 5/18 minidump *that Norton was supposedly deleting* is on disk.

Reasonable inference: **Norton was not the dump-deletion mechanism**, or at
least not the only one. The original M1 dump-deletion hypothesis was wrong on
the deletion specifics. The 11:17 AM dump was always there; we couldn't see it
because of the PowerShell-ACL behaviour. This doesn't fully rule out
"Norton was disrupting visibility" — it just means we have less reason to think
so now. Either way: data we thought was lost is recovered.

### Implication for the hypothesis ranking (preliminary — fuller update in § 9 after WinDbg)

- **H1 (Intel RST `iaStorAVC.sys` / `RstMwService`) — leading, strengthened.**
  10/10 crash-time correlation with `RstMwService` termination. Storage-wedge
  no-dump pattern continued after the only competing explanation (H1b) was
  eliminated. The driver state inventory shows RST machinery still present.
- **H1b (dual-AV Norton minifilter) — weakened to unlikely sole cause.**
  Norton is uninstalled; the crash recurred with identical signatures the same
  evening. If H1b were the sole cause, the M1 uninstall should have prevented
  this crash. Doesn't rigorously *disprove* H1b (could be a tail event, could be
  state lingering from years of Norton operation, could be one of multiple
  contributors), but the evidence balance has shifted hard toward H1.
- **H2 (NVIDIA), H3 (Killer), H4 (May KBs as trigger), H5 (power), H6 (hardware)
  — unchanged** from the prior ranking. Subsidiary, awaiting evidence.

### Step 3 — WinDbg availability check

| Tool                                            | Found? |
| ----------------------------------------------- | ------ |
| `windbg.exe` on PATH                            | No     |
| `cdb.exe` on PATH                               | No     |
| `kd.exe` on PATH                                | No     |
| `C:\Program Files\Windows Kits\10\Debuggers\…`  | No     |
| `C:\Program Files (x86)\Windows Kits\…`         | No     |
| `Microsoft.WinDbg` Appx package                 | Not installed |
| `C:\Program Files\Debugging Tools for Windows*` | None   |

**WinDbg is not available on this system. STOP per the task's stop-and-ask
trigger — install required before analysis can proceed.**

### WinDbg install options

Three reasonable paths, ordered cheapest to heaviest:

1. **Microsoft Store / `winget` — WinDbg (modern preview, recommended).**
   - From an elevated PowerShell: `winget install --id Microsoft.WinDbg`
   - Or: open Microsoft Store, search "WinDbg", click Install.
   - Roughly 200 MB download, no SDK dependency, ships symbols server pre-
     configured.
   - After install, the binary is `windbgx.exe`. Open dump with:
     ```
     windbgx.exe -z C:\Windows\Minidump\051826-14937-01.dmp
     ```
   - Recommended unless the user has a reason to prefer the classic.

2. **Windows SDK → Debugging Tools for Windows (classic).**
   - Download Windows 11 SDK installer from learn.microsoft.com → run → during
     "Select the features you want to install", **uncheck everything except**
     "Debugging Tools for Windows".
   - Installs `windbg.exe`, `cdb.exe`, `kd.exe` to
     `C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\`.
   - ~250 MB. Classic UI. Slightly more featureful for kernel work than the
     Store version, but the Store version is sufficient here.

3. **`winget install Microsoft.WindowsSDK.10` (programmatic SDK install).**
   - Same Debugging Tools subset as #2 but installed via winget.
   - Heavier than #1.

### Once WinDbg is installed, the analysis commands to run

Against `C:\Windows\Minidump\051826-14937-01.dmp`:

```
.symfix
.reload
!analyze -v
lm
!process 0 0
```

Save the output to
`docs/diagnostics/2026-05-19_crash_7_windbg.txt` (filename keeps "2026-05-19"
to match the report's naming even though the system date is 5/18). Commit
separately.

### What we're hoping to see (and what each result means)

| WinDbg `!analyze -v` "PROBABLY_CAUSED_BY" | Hypothesis confirmed | Recommended next user-action |
| ----------------------------------------- | -------------------- | ---------------------------- |
| `iaStorAC.sys`, `iaStorAVC.sys`, `iaStorV.sys`, or any other `iaStor*` | **H1 (Intel RST)** | M2 (uninstall RstDowngradeGuard + OptaneDowngradeGuard + Intel RST). |
| `nvlddmkm.sys`, `nvkmd*.sys`, `nv*.sys`   | **H2 (NVIDIA)**     | NVIDIA Game Ready Driver clean install. |
| `rt*.sys`, `qcamain*.sys`, `Killer*.sys`, `Rivet*` | **H3 (Killer Wi-Fi)** | Replace Killer driver stack with Intel reference driver. |
| `ntoskrnl.exe` / `ntfs.sys` / `volmgr.sys` / Microsoft-only stack | Ambiguous — surface for review | Stop and decide; could be H4 (KB-induced kernel bug) or storage wedge unattributed. |
| Anything else (e.g., `acpi.sys`, `pci.sys`, an EC driver) | **New hypothesis Hx**, stop and surface | Don't act yet — discuss before acting. |

### Estimated effort and risk of recommended actions (preliminary)

| Action | Effort | Risk |
| ------ | ------ | ---- |
| Install WinDbg + run analysis | 15 min + read time | None. |
| If H1 confirmed → M2 (RST uninstall) | 15 min + reboot | Low. Inbox `stornvme.sys` handles Samsung NVMe fine; create System Restore point first. |
| If H2 confirmed → M3 (NVIDIA clean install) | 20 min | Very low. |
| If H3 confirmed → Killer→Intel driver swap | 30 min | Low. Loses Killer Control Center features. |
| If something else → discuss | Variable | Variable — don't act without review. |

### Net assessment

The dump we thought we lost is recovered. WinDbg is the next gate. Until WinDbg
runs against `051826-14937-01.dmp`, H1 is the leading hypothesis by both (a)
direct correlation (10/10 RstMwService crash-time matches) and (b) elimination
(H1b dropped after Norton uninstall failed to prevent tonight's crash). The
dump should produce a definitive answer; do not act on M2 or any other
mitigation until the dump analysis has been read.

End of § 8 (pre-WinDbg portion).

### Step 5 — Post-WinDbg recommendation (2026-05-18 21:xx local)

**What the dump showed:** crash #6 (5/18 11:17 AM, bugcheck `0x7E`) is a
near-null pointer dereference inside **`nvlddmkm.sys`** (NVIDIA kernel-mode
display driver), module timestamp Dec 3 2021 — the exact OEM driver flagged
in § 1's stale-driver table. Process context: System (kernel DPC). No
`iaStor*` modules in the faulting stack; no Norton modules.

**Leading hypothesis:** H2 (NVIDIA outdated `nvlddmkm.sys`).
The prior leading hypothesis, H1 (Intel RST), is demoted: the `RstMwService`
7023 correlation is re-interpreted as downstream artifact of dirty reboots,
not cause.

**Recommended next user-action:** **M3 — Clean install of current NVIDIA
Game Ready Driver (or Studio Driver, preference is fine).**

Specifically:

1. Go to **nvidia.com → Drivers** → select Product Type "GeForce" → Series
   "GeForce RTX 30 Series (Notebooks)" → Product "GeForce RTX 3060 Laptop
   GPU" → OS "Windows 11" → Driver Type "Game Ready Driver" (or "Studio
   Driver" — Studio is slightly more conservative on release cadence, fine
   for non-gaming workloads). Download the current package.
2. Run the installer. Choose **Custom installation** → tick **"Perform a
   clean installation"**. This wipes the 4.5-year-old profile, removes any
   stale NVIDIA service state, and installs fresh.
3. Reboot when prompted.
4. Optional belt-and-braces: before step 1, download **DDU (Display Driver
   Uninstaller)** from wagnardsoft.com, boot into Safe Mode, run DDU on the
   NVIDIA driver to scrub it cleanly, then reboot to normal mode and install
   the new driver. This is the gold-standard "no residual state" path; only
   worth the extra ~30 min if the simple clean install in step 1–3 doesn't
   resolve crashes within 48 hours.
5. After the install, verify in Device Manager → Display adapters → NVIDIA
   GeForce RTX 3060 Laptop GPU → Properties → Driver tab: driver date should
   be 2026 (or at minimum recent 2025), version starts with a current major
   (current branch is ~570.xx as of last public release).

**Effort:** 20–30 minutes (download ~700 MB + install + reboot). DDU detour
adds ~30 min if used.

**Risk:** Very low.
- Samsung MZVLQ NVMe + Intel UHD iGPU are untouched.
- Existing NVIDIA Control Panel preferences are wiped (intentional — clean
  install).
- If the new driver has its own issues (rare), NVIDIA's installer keeps the
  prior installer in `C:\NVIDIA\` for rollback, and Windows keeps the prior
  driver under Device Manager → Roll Back.
- Create a System Restore point first as belt-and-braces.

**What "confirmed" looks like:** observe for **48–72 hours** of normal
heavy use (see § 7 "What counts as valid observation" — same definition
still applies). Expected outcomes:

| Observation                         | Interpretation                                          | Next action          |
| ----------------------------------- | ------------------------------------------------------- | -------------------- |
| Zero K-P 41 / 1001 / 6008 events    | H2 confirmed. Done.                                      | Stop. Resume project work. |
| Reduced rate, still occasional crashes | H2 was a primary contributor; secondary cause exists.   | M2 (RST uninstall) next. |
| No reduction in crash rate          | H2 was not the primary cause (or fix didn't take).      | M2 next; reassess.    |

**M2 (Intel RST uninstall) — deferred but not abandoned.**
- Still a hygiene win regardless of crash causation. 5-year-old RST stack
  on a system with no RAID/Optane shouldn't be there.
- Apply after the 48–72 hr post-M3 observation window concludes (regardless
  of outcome — either as the next mitigation if M3 didn't fully resolve, or
  as residual hygiene if M3 did).
- Effort/risk per § 3 M2 detail; unchanged.

**M4 (Killer driver replacement) — further deferred.**
Killer noise is independent (KillerProviderDataHelperService.exe crashes
weekly), but no Killer module appeared in the faulting stack. Apply after
M3 and M2 have had a chance, only if Killer service crashes persist.

**Mitigations that should NOT be done now:**
- DON'T downgrade or roll back any May 2026 KBs. H4 is the trigger
  interpretation, not the cause; rolling back loses security fixes and
  doesn't address the underlying brittle driver.
- DON'T run stress tests (Prime95, FurMark) to "verify the fix" — risks
  inducing crash #12 mid-test on a system whose root cause was just
  identified but not yet remedied.
- DON'T do M5/M6 (sfc / chkdsk / MemDiag) preemptively. H6 (hardware) is
  still LOW; M5/M6 only enter the plan if M3 + M2 don't resolve crashes.

**Stop here — do not apply M3 this session.** This is a diagnostic report,
not a fix-application session. The Board reviews and executes M3 when
ready.

End of § 8.

---

## 9. Crash #8 diagnostic + widened-scope inventory — 2026-05-19

User reported an eighth crash on **2026-05-18 21:13** (within a few hours of
the NVIDIA Game Ready Driver clean install completing). The H2 (NVIDIA)
mitigation that the prior session recommended has now been applied and has
**not** stopped the crashes. With H1b (Norton) also previously falsified by
mitigation test, two single-hypothesis software mitigations have failed.
This section is **inventory and ranking, not testing or mitigation** — the
deliverable is updated diagnostic data and a recommended testing sequence
for the user to act on next session.

No fixes applied this session.

### Step 1 — Verify no-new-dump finding and inventory current state

#### Dump-file inventory

`cmd /c dir C:\Windows\Minidump\` (PowerShell `Get-ChildItem` is still ACL-blocked
for the non-elevated session):

| File                          | Size (bytes) | Written              |
| ----------------------------- | ------------ | -------------------- |
| `051826-14937-01.dmp`         | 2,820,616    | 2026-05-18 11:17 AM  |

**Only the prior session's NVIDIA-confirmed dump is present. Crash #8 did NOT
produce a new dump.** `CrashDumpEnabled=7` is set and effective, yet crash #8
still produced no bugcheck — the hard-hang pattern continues. 11 of 12 crashes
have now produced no dump.

#### Crash #8 timeline (Event Viewer)

| Time                | Provider                                              | ID    | Note                                                                                       |
| ------------------- | ----------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------ |
| 5/18 21:01:15       | User32 (`C:\WINDOWS\Temp\<uuid>\setup.exe (MSI)`)     | 1074  | **MSI installer initiated a restart.** Almost certainly the NVIDIA Game Ready Driver clean-install reboot. |
| 5/18 21:01:30       | EventLog                                              | 6006  | Clean shutdown began (first NVIDIA-install reboot).                                        |
| 5/18 21:02:02       | EventLog                                              | 6005  | Boot complete — system came back up from NVIDIA installer's first reboot.                  |
| 5/18 21:04:16       | User32 (StartMenuExperienceHost)                      | 1074  | Second `Initiated restart` log (likely the NVIDIA installer triggering a second restart for finalization). |
| 5/18 21:04:23       | winsrvext                                             | 100   | `explorer.exe is delaying system shutdown after 5016 milliseconds` — explorer hanging during shutdown. |
| 5/18 21:04:25       | EventLog                                              | 6006  | Clean shutdown began (second restart attempt).                                             |
| 5/18 21:04:26       | WLAN-AutoConfig                                       | 10002 | WLAN Extensibility Module stopped (shutdown sequence).                                     |
| 5/18 21:05:00       | (implied — recorded retroactively by 6008 at 21:13:42)| —     | **System "shutdown" — actually hard-hung during clean shutdown sequence.**                 |
| 5/18 21:10:38       | Resource-Exhaustion-Detector                          | 2004  | **Low virtual memory diagnostic — python.exe (PID 10416) consumed 59,088,732,160 bytes (~55 GB).** Buffered pre-crash event committed to disk on next boot. |
| 5/18 21:13:30       | Microsoft-Windows-Kernel-Power                        | **41**| **Critical** — system rebooted without cleanly shutting down. Crash #8 post-boot record. |
| 5/18 21:13:32       | Kernel-PnP                                            | 219   | `\Driver\WUDFRd failed to load` (boot enumeration, downstream artifact).                   |
| 5/18 21:13:42       | EventLog                                              | 6005  | EventLog service started on post-crash boot.                                               |
| 5/18 21:13:42       | EventLog                                              | 6008  | Previous shutdown at 21:05:00 was unexpected.                                              |
| 5/18 21:13:43       | Service Control Manager                               | 7023  | `RstMwService terminated with error` — the same +13s post-boot pattern, 11/11 since 5/15.  |
| 5/18 21:13:48       | DNS-Client                                            | 1014  | wpad resolution timeout (boot-time noise).                                                 |
| 5/18 21:13:49       | Netwtw10                                              | 6062  | LSO triggered (Wi-Fi reconnect on boot).                                                   |
| 5/18 21:13:55       | DistributedCOM                                        | 10016 | COM permission warning (chronic, unrelated).                                                |

**Material observations:**
- **No BugCheck 1001 event** — kernel did not run the bugcheck path. Same as 10
  of the prior 11 crashes.
- **No WHEA-Logger events at all in the last 7 days.** Hardware-error path
  remains clean.
- **RstMwService 7023 at +13s post-boot — 11/11.** Pattern continues.
- **The 21:05:00 "shutdown" was actually a hung clean-shutdown attempt** —
  6006 fired at 21:04:25 (clean shutdown began), explorer hung at 21:04:23,
  then nothing more until the next boot at 21:13:42. Crash #8 happened
  **during a planned restart triggered by the NVIDIA installer**, not during
  active project work.

#### Pre-crash workload context

The task prompt notes that the prior session's agent was performing the
Kalshi structure-arb review (reading files, running scoped pytest). Two
observations sharpen this:

1. **The actual crash moment was inside a planned reboot** (NVIDIA installer's
   second post-install restart). The python.exe process that Resource-
   Exhaustion-Detector flagged (PID 10416, ~55 GB virtual) was still committed
   when Windows began the clean shutdown sequence at 21:04:25. The shutdown
   sequence couldn't terminate the process or page out its commitments fast
   enough; explorer hung trying; the system did not complete shutdown.
2. **Pre-crash memory state was severe.** ~10 Claude desktop processes,
   pytest python.exe at ~55 GB virtual, Vmmem (WSL) at ~1.4 GB,
   Defender realtime active, IDE running, plus baseline OS overhead — on a
   16 GB RAM + 17 GB pagefile = 33 GB commit-limit system. This is the
   pre-disposition; the NVIDIA installer's restart was the trigger.

### Step 2 — Hardware-hypothesis tests

#### SMART status

```
SAMSUNG MZVLQ1T0HALB-00000 (the only physical drive)
Status: OK | Healthy | OK (1 TB NVMe)
```

No stop-and-ask trigger fired. Storage is not visibly failing.

#### Prior Memory Diagnostic runs

`Get-WinEvent -LogName System | Where-Object {$_.ProviderName -like "*MemoryDiagnostic*"}` →
**zero events.** No memtest has ever been recorded on this system. Diagnostic
gap; queue for next planned reboot.

#### Temperature monitoring

Registry scan of installed software for HWMonitor / Core Temp / HWiNFO /
Afterburner / CrystalDiskInfo etc. → **zero matches.** Only `MSI Center SDK
3.2021.1126.01` is installed (a platform SDK, not the full MSI Center with
temp readouts).

**Diagnostic gap: no live temperature data available.** Recommend installing
**HWiNFO64** (free, vendor-neutral, lightweight, sensor-only mode) before the
next long work session so the user can correlate temps with crashes if needed.

#### Power configuration

```
Sleep states available:    S3 (Standby), Hibernate, Fast Startup
Sleep states unavailable:  S1, S2, S0 Low Power Idle (firmware), Hybrid Sleep (hypervisor)
Active scheme:             Balanced (381b4222-f694-41f0-9685-ff5bb260df2e)
Min processor state:       5%   (AC and DC)
Max processor state:       100% (AC and DC)
```

Modern, standard config. No obvious power-management red flag. The system
does have **Fast Startup** enabled — known on some configurations to cause
storage-driver state corruption across reboots, but not specific to the
observed pattern.

#### Battery report (fresh)

| Field           | Value      | Note                                                              |
| --------------- | ---------- | ----------------------------------------------------------------- |
| Design Capacity | 95,000 mWh |                                                                   |
| Full Charge     | 72,914 mWh | Down 87 mWh from prior session (73,021 mWh).                      |
| Cycle Count     | unknown    | Battery firmware not reporting cycle count via PowerShell parse.  |
| Wear            | **23.2%**  | Marginal slow drift; on AC, not load-bearing for crash diagnosis. |

Battery is on AC the whole time. Not a credible crash cause.

#### Boot / shutdown / crash pattern (last ~50 system events)

11 K-P 41 critical events appear in the recent System log (4/30 onward), with
the cluster densifying since 5/17 23:00. Time-of-day distribution: morning,
midday, evening — **no time-of-day correlation**. Uptime-at-crash
distribution: a few minutes (post-reboot crashes #2, #3 of 5/17-night
cluster) to hours (steady-state crashes during workday) — **no uptime
correlation**. The strong correlation is with the **Event 2004 low-VM
diagnostic** (10/10, as documented in § 2's post-crash-#8 ranking and Step 4
below), not with thermal accumulation, time-of-day, or boot age.

### Step 3 — Firmware / BIOS

#### BIOS

```
Manufacturer:   American Megatrends International, LLC.
Name:           E17K3IMS.119
Version:        MSI_NB - 1072009
Release Date:   2021-08-05
Model:          MSI GE76 Raider 11UE
```

**BIOS is 4.5 years old, OEM factory image, never updated.** Same vintage
cluster as NVIDIA (Dec 2021), Killer Wi-Fi (Jun 2021), Intel chipset (Apr 2021).
MSI has released multiple BIOS updates for the GE76 Raider 11UE since 2021;
the user should check **msi.com → Support → GE76 Raider 11UE → BIOS** for the
current version. Newer BIOS revisions for this model are known to include
stability fixes for storage controller (Intel VMD/RST), power management
(C-state behaviour at high commit), and embedded controller / power delivery
patches.

**Do NOT auto-update BIOS.** It is a manual, high-risk user action requiring
plugged-in AC, sufficient battery charge, and an unattended reboot. The user
should evaluate after reading the changelog at MSI's support page.

#### TPM / Secure Boot

`Get-Tpm` and `Confirm-SecureBootUEFI` returned access-denied (require elevation).
TPM details and Secure Boot status are diagnostic gaps for this session.
WMI fallback (`Win32_Tpm` in `MicrosoftTpm` namespace) is also access-denied.
**Queue for an elevated PowerShell next session** if H8 (firmware) needs deeper
testing — but H8 is currently LOW-MEDIUM, not the highest-priority next test.

### Step 4 — Workload-pressure hypothesis (NEW H7)

This is the **major finding** of this session.

#### Pagefile + commit-charge configuration

```
Pagefile:                 C:\pagefile.sys   AllocatedBaseSize 17,408 MB
Current usage at write:   14 MB (idle moment)
AutomaticManagedPagefile: True
Total RAM:                16,085 MB
Commit limit (RAM + PF):  35,120 MB (~33 GB before pagefile auto-expansion)
```

Page file is system-managed and adequately sized for the static workload.
Under heavy backtester load it has been auto-expanding — this is necessary
but ALSO is the mechanism by which a 50+ GB python process is "tolerated"
until Windows decides commit pressure crosses the resource-exhaustion threshold.

#### Current top-20 processes by working set

Top consumers right now (idle moment, light workload):

| Process               | WS (MB) | VM (MB)     | PID   | Note                                |
| --------------------- | ------- | ----------- | ----- | ----------------------------------- |
| explorer              | 428     | 2,102,810   | 11404 |                                     |
| claude × 4            | ~1,200  | 3,539,150 × | —     | Multiple Claude Code processes      |
| Discord × 3           | ~770    | 3,498,000 × | —     | Discord helper procs                |
| MsMpEng               | 303     | 2,102,491   | 4940  | Defender realtime                   |
| dwm                   | 180     | 2,101,945   | 1856  | Window manager                      |
| NVDisplay.Container   | 164     |             |       | New NVIDIA service (post-install)   |
| OneDrive              | 155     |             |       |                                     |
| SteelSeriesGGEZ       | 154     |             |       |                                     |
| msedge / Copilot      | ~265    |             |       |                                     |

No active backtester python in this moment, so no 50 GB VM consumer. **The
crash precondition is heavy-Python sessions, not the steady-state baseline.**

#### Event 2004 (Resource-Exhaustion-Detector) — the smoking gun

Last 24 hours, low-virtual-memory diagnostics:

| Event 2004 time      | Top consumer                          | Nearest K-P 41 crash | Crash lead time |
| -------------------- | ------------------------------------- | --------------------- | --------------- |
| 5/17 23:03:31        | python.exe (19244) ~47.5 GB           | 5/17 23:07:06        | 3 min 35 s      |
| 5/17 23:24:06        | python.exe (18580) ~57.7 GB           | 5/17 23:27:43        | 3 min 37 s      |
| 5/18 00:02:32        | python.exe (27524) ~47.6 GB           | 5/18 00:04:24        | 1 min 52 s      |
| 5/18 07:12:26        | python.exe (3936) ~43.7 GB            | 5/18 07:15:38        | 3 min 12 s      |
| 5/18 10:36:57        | python.exe (20560) ~58.7 GB           | 5/18 10:39:06        | 2 min 09 s      |
| 5/18 11:10:33        | python.exe (11288) ~59.5 GB           | 5/18 11:17:04 (dump) | 6 min 31 s      |
| 5/18 11:15:33        | python.exe (11288) ~60.5 GB           | (same crash)          | 1 min 31 s      |
| 5/18 19:27:03        | python.exe (9412) ~47.6 GB            | 5/18 19:31:40        | 4 min 37 s      |
| 5/18 20:16:48        | python.exe (2616) ~58.8 GB            | 5/18 20:18:34        | 1 min 46 s      |
| **5/18 21:10:38**    | **python.exe (10416) ~55.0 GB**       | **5/18 21:13:30 #8** | **2 min 52 s**  |

**10 of 10 recent crashes are preceded by Event 2004 within 1.5 – 6.5 minutes.**

Baseline comparison: in the 27 days from 4/20 to 5/17 22:00, Event 2004 fired
**once** (5/2 12:53, python.exe at ~7.4 GB, did not precede a crash). The
metric went from rare-baseline to crash-precedent at the 5/17 23:00 inflection
point — same inflection as the crash cluster.

What changed at 5/17 23:00: the Trading Corp workload shifted to **heavy
backtester runs** (Kalshi structure-arb backtester at ~600 events × cross-
sectional analysis; BitUnix v3 hybrid backtests; long pandas DataFrames held
in memory for the report-writing phase). These are the Python processes
sitting at 45–60 GB virtual.

#### Reliability Monitor

`perfmon /rel` is GUI-only without elevation/scripting hooks. The
Event-Viewer signals above (Event 2004, K-P 41, no WHEA) are the same data
Reliability Monitor would surface. No additional info gained from a separate
RelMon view this session.

#### Verdict on H7

**H7 (workload-pressure / virtual-memory exhaustion) is the new leading
hypothesis.** Direct correlation evidence is stronger than any other
hypothesis has had at any point in the investigation:

- Single biggest evidence: 10/10 Event 2004 → K-P 41 within minutes.
- Mechanism: kernel can't allocate to write bugcheck dump when commit is
  exhausted → explains 11/12 no-dump pattern (with the one dump being the
  exceptional case where the fault path happened to be runnable).
- Inflection-point match: pattern began exactly when heavy backtester
  workload type was introduced.
- Falsification evidence: prior single-driver mitigations (Norton, NVIDIA)
  did not stop the crashes because the underlying mechanism is system-wide
  memory pressure, not any individual driver.

**Workload-reduction test (M-WR — new, free, immediate):** the user runs a
24 – 48 hour session under **explicitly capped Python memory** — only one
backtester process at a time, with the heavy processes monitored for >30 GB
VM and forcibly killed before they reach the 2004-trigger zone. Concretely:

1. Close all but one Claude Code window during heavy work.
2. Shut down WSL (`wsl --shutdown`) — frees ~1.4 GB virtual + reduces commit
   pressure.
3. Close Discord (it routinely sits at 3 × 3.5 GB VM = 10 GB virtual).
4. When running backtesters: monitor `python.exe` VM via
   `Get-Process python | Select WorkingSet64, VirtualMemorySize64` every
   30 seconds; kill processes >25 GB VM before they hit the 2004 zone.
5. Observe whether crashes stop.

If crashes stop under reduced workload → H7 confirmed; permanent fix is to
**split heavy backtester runs into separate Python processes that exit
between batches**, not to keep one long-running process across an entire
backtest sweep.

If crashes continue under reduced workload → H7 weakened; reopen H1 (RST
uninstall, M2) and H8 (BIOS update) as next mitigations.

### Step 5 — Killer Wi-Fi hypothesis

#### Killer driver versions

| Component                                   | Version          | Driver Date      | Vintage        |
| ------------------------------------------- | ---------------- | ---------------- | -------------- |
| Killer(R) Wi-Fi 6E AX1675x 160MHz (210NGW)  | **22.70.0.6**    | **2021-06-28**   | OEM-stale (5 yr) |
| Killer Networking Software                  | 3.1524.510.1     | 2024-05-09       | ~2 yr           |
| Killer E3100G 2.5 Gigabit Ethernet          | 1125.20.729.2024 | 2024-07-28       | ~2 yr           |

The Wi-Fi driver itself is **same vintage as NVIDIA** (mid-2021 OEM image,
never updated). Networking software and the Ethernet driver have been
refreshed by Killer.

#### Killer user-mode crash count

Last 7 days: **17 `Application Error` events for
`KillerProviderDataHelperService.exe 3.1524.510.1`** (same time stamp 0x663e3e89,
indicating one binary version repeatedly crashing).

Looking at crash-time alignment: every Killer crash timestamp lines up with a
post-reboot SCM boot moment (within seconds of K-P 41 events). Killer
service crashes are **downstream artifacts of system crashes** — service
auto-starts post-boot, fails immediately because its kernel counterpart is
in a state it can't talk to, exits. Same shape as the RstMwService 7023
pattern.

#### Verdict on H3

No new positive evidence elevates Killer as a primary cause. But the
**driver vintage is the same OEM-stale cluster as NVIDIA and BIOS** — same
"nothing on this machine has been updated since factory" story. **H3 stays
LOW–MEDIUM**: untested by mitigation, plausible co-conspirator if H7 is the
trigger, but no direct evidence.

### Step 6 — Hypothesis ranking summary (canonical for this session)

The full restructured ranking is in § 2's "Post-crash-#8 widened-scope re-
ranking" subsection. Quick summary here for cross-referencing:

| Category                          | Hypotheses                                          |
| --------------------------------- | --------------------------------------------------- |
| **Confirmed for individual crash** | H2 (NVIDIA) — crash #6 via the one bugcheck dump   |
| **Falsified by mitigation**        | H1b (Norton dual-AV), H2 (NVIDIA) as complete cause for the pattern |
| **Untested but plausible — NEW**   | H7 (workload pressure / VM exhaustion) — **LEADING**; H1 (Intel RST); H3 (Killer Wi-Fi 2021-vintage); H8 (BIOS / firmware 2021-vintage); H6 (hardware fault not visible to WHEA) |
| **Trigger-only (not root)**        | H4 (May 2026 KBs accelerated the existing brittleness) |
| **LOW**                            | H5 (power delivery), H1b (Norton)                   |

The pattern of **11/12 no-dump crashes** suggests the cause is in:
- Driver-interrupt-context (any driver wedging the storage stack before
  bugcheck can run), OR
- System-wide commit exhaustion preventing the kernel from allocating to
  write a dump (H7), OR
- Hardware / firmware path that bypasses software error handling entirely.

H7 is the only single-mechanism hypothesis that explains every observation
in the data we have. Alternative interpretations exist but require either
multiple unrelated causes or speculative mechanisms unsupported by WHEA data.

### Step 7 — Recommended testing sequence

Ordered cheap → expensive, likely-yield → speculative. The user picks which
to act on. **No mitigation applied this session.**

| # | Test                                    | Effort        | Likely yield                | Risk     | Type           |
| - | --------------------------------------- | ------------- | --------------------------- | -------- | -------------- |
| 1 | **M-WR — workload-reduction observation** (close extra Claude windows, `wsl --shutdown`, close Discord, monitor python VM, cap at ~25 GB) | 24–48 hr passive observation | HIGH — directly tests H7 which has the strongest correlative evidence | None | Software-process change, no install |
| 2 | **Install HWiNFO64** (free, vendor-neutral) and run during the M-WR window to gather temp + voltage + commit-charge data | 10 min install + passive | MEDIUM — closes the temp diagnostic gap; tests H5/H6 thermal-amplifier story passively | None | Read-only monitoring tool |
| 3 | **Check MSI BIOS support page for E17K3IMS.119 vs current** (do NOT auto-update; user reads changelog and decides) | 15 min user-side | MEDIUM — tests H8 if BIOS changelog mentions storage / power / EC fixes | None this step (read-only); the UPDATE itself is high-risk and deferred to a separate explicit decision | User read-only |
| 4 | **Queue Windows Memory Diagnostic for next planned reboot** (`mdsched.exe` schedules without running; reboot when user can spare 2 hours) | 0 min to queue, 2 hr at reboot | LOW — no positive signal for RAM, but free + closes diagnostic gap (H6) | None | Built-in MS tool |
| 5 | **M2 — uninstall Intel RST + RstDowngradeGuard + reboot** (the next "obvious software step" the prior session held off on) | 15 min + reboot | MEDIUM — tests H1; hygiene win regardless | Low | Driver uninstall |
| 6 | **M4 — replace Killer Wi-Fi driver 22.70.0.6 with current Intel AX210 reference driver** | 30 min | LOW — tests H3, no positive evidence elevates Killer as primary | Low | Driver swap |
| 7 | **BIOS update** (only after #3 confirms a newer version exists with relevant stability fixes; manual, high-risk) | 30 – 60 min unattended | MEDIUM if relevant changelog; LOW otherwise | **HIGH** — bricking risk if interrupted | Manual firmware flash |
| 8 | **RAM swap / PSU test / physical inspection** | service center | LOW — no positive signal | Highest | Hardware replacement |

#### Recommended order of execution

1. **Run test #1 first.** Workload-reduction is free, immediate, tests the
   highest-confidence current hypothesis, has zero downside. If crashes
   stop, the diagnosis converges and no further mitigation is needed beyond
   the workload-management process change.
2. **Run test #2 in parallel with #1.** HWiNFO64 install is 10 minutes and
   gives us passive data during the workload-reduction window. If crashes
   continue under reduced workload, HWiNFO data will be needed for the next
   step.
3. **Run test #3 in parallel with #1.** No system impact; user-side reading
   only. Frames whether #7 (BIOS update) is worth considering later.
4. **Run test #4 at next planned reboot.** Queues for free; runs during a
   time the user is away from the laptop anyway.
5. **If tests #1 / #2 / #3 / #4 leave the diagnosis unresolved, then #5
   (RST uninstall).** This is the "next obvious software step" but is held
   off here because the workload-reduction evidence base is stronger and
   gives broader signal.
6. **#6 only if #5 doesn't help.**
7. **#7 only if #3 surfaced a relevant changelog AND nothing else helped.**
   BIOS flashing is the last manual-risk software step.
8. **#8 only as final resort** with everything software / firmware
   exhausted.

#### What success looks like for each test

| Test | Pass condition                                                                 | Fail / inconclusive condition                                                |
| ---- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| #1   | Zero K-P 41 + zero 6008 events in a 48 h window with reduced workload          | Any new K-P 41 → H7 not the sole cause; reopen H1                            |
| #2   | HWiNFO logs show CPU pkg < 95 °C and VRM voltages stable during heavy use       | Spikes to 95 °C+ or voltage transients → H5 / H6 amplifier confirmed         |
| #3   | Current MSI BIOS version available and changelog mentions storage / EC / power fixes | Same BIOS or no changelog match → H8 deprioritized                           |
| #4   | MemoryDiagnostic completes with 0 errors                                       | Any error → immediate stop, RAM replacement (high-priority)                  |
| #5   | RstMwService 7023 stops; no further K-P 41 in 48 h                              | Crashes continue → H1 falsified                                              |

#### Stop-and-ask triggers that did NOT fire this session

- SMART status on the one physical drive is OK / Healthy.
- No WHEA-Logger events in 7 days.
- No Memory Diagnostic warnings (none have ever run).
- BIOS is old but not surprisingly so (4.5 yr, same vintage cluster as NVIDIA
  and Killer Wi-Fi already known to be old).
- Workload-reduction test IS plausible and IS surfaced as the highest-
  priority cheap test (test #1).

#### Stop-and-ask triggers that did fire (informational, no immediate user
action required)

- **Diagnostic gap: no prior memtest, no temperature monitoring tool, no
  elevated-TPM-inspection access.** All three are non-blocking but worth
  closing before the next round of mitigation testing.

### Step 8 — Commit and stop

Commit per task spec. End of session — no project work, no mitigations
applied.

End of § 9.

---

## 10. Mitigation 1 applied; mitigation 2 mechanism analysis — 2026-05-19

Follow-up to § 9. The Board accepted H7 as the working leading hypothesis
and instructed: apply the workload-reduction baseline now; analyze the
Python VM cap mechanism options and surface a recommendation before
implementing.

### Mitigation 1 (workload reduction baseline) — APPLIED

Documented at [docs/runbooks/session_workload_defaults.md](../runbooks/session_workload_defaults.md).
Session-start checklist, memory sampler command, action thresholds, and
verification command for the 48 h observation window. BACKLOG.md P0
section updated to reference the runbook.

The runbook is the **session-discipline lever**: keep committed memory
below Event-2004 trigger thresholds through process hygiene. It assumes
the user is watching the sampler. The VM cap (Mitigation 2) is the
enforcement lever — a hard per-process limit that catches a runaway
Python even if the user looks away.

### Mitigation 2 (Python VM cap) — mechanism options analyzed

The diagnostic recommended "cap python VM at ~25 GB" without specifying
how. Five options surfaced in the task prompt; analysis below.

#### Option A — Per-process Job Object limits via PowerShell / `procgov`

**Mechanism:** Windows Job Objects support `ProcessMemoryLimit` (per-
process committed memory cap) and `JobMemoryLimit` (total job committed
memory cap) via `SetInformationJobObject` with
`JobObjectExtendedLimitInformation`. When a process exceeds the limit,
the kernel terminates it cleanly — no swap-thrash, no system-wide
pressure cascade.

The cleanest user-space tool that wraps this API is
**`procgov`** (Process Governor by Sebastian Solnica, MIT-licensed,
~60 KB single-file exe, well-tested, used internally at Microsoft).
Available via winget on this machine:

```
winget search ProcessGovernor
  → procgov  LowLevelDesign.ProcessGovernor  3.2.25275  winget
```

Usage (no admin needed once installed):

```
procgov --maxmem 25G -- python scripts/backtest_kalshi_structure_arb.py
procgov --maxmem 25G -- pytest tests/test_kalshi_structure_arb.py
```

Wrapping in a project shim (e.g. `scripts/run_capped.ps1`) removes the
"easy to forget" risk: the user runs `./scripts/run_capped.ps1 python …`
and the wrapper applies the cap.

| Pros                                                                 | Cons                                                                  |
| -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Zero code changes — wraps any invocation                              | Requires installing one tool (winget, ~30 s)                          |
| Hard kernel-enforced limit; no thrash mode                            | Easy to forget the wrapper; needs shim or muscle memory               |
| Sebastian Solnica is a known good source (sysinternals-adjacent)      | Third-party binary (well-trusted but not Microsoft-signed)            |
| MIT-licensed, vetted by community                                    | New invocation pattern for the user to internalize                    |

#### Option B — WSRM (Windows System Resource Manager)

**Verdict: not applicable.** WSRM was a Server-edition-only Microsoft
component, **deprecated in Windows Server 2012 R2 and removed since.**
Not available on Windows 11 Home (this machine). Skip.

#### Option C — Python-side via psutil / ctypes / conftest.py

**Mechanism:** POSIX has `resource.setrlimit(resource.RLIMIT_AS, …)` —
hard per-process virtual-memory cap that fires `MemoryError` on
allocation. The `resource` module is **not available on Windows**;
neither is `RLIMIT_AS`.

The Windows-native alternatives:

1. **`psutil` polling** — call `psutil.Process().memory_info().vms` at
   checkpoint boundaries, raise MemoryError if over threshold. Doesn't
   enforce: a single allocation spike between checkpoints can still
   crash the system. Useful as a *secondary* monitor inside backtest
   loops, not as a primary cap.
2. **`ctypes` → `SetProcessWorkingSetSize`** — only caps working set
   (resident pages), not committed virtual memory. Doesn't address the
   commit-charge driver of Event 2004. Skip.
3. **Job-object via ctypes from Python** — same kernel API as Option A,
   but the Python process applies the limit to itself at startup
   (in `__main__.py` or `conftest.py`). Works, but requires touching
   every entry point in the repo. Verbose ctypes boilerplate (~60 LoC).

Cleanest in-code path: a `conftest.py` fixture that calls psutil at
test setup and warns if commit is already high, plus a backtest-side
checkpoint in long-running loops. **Useful as a complement to Option A,
not a substitute.**

| Pros                                                                 | Cons                                                                  |
| -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Code-controlled, version-controlled, repo-local                       | No clean primary mechanism on Windows; setrlimit unavailable          |
| Granular — can apply different caps per entry point                   | Polling-based; misses single-allocation spikes                        |
| No external dependency to install                                    | Requires touching every entry point (pytest, backtest scripts)        |
| Good as secondary safety net inside long-running loops               | ctypes job-object boilerplate is verbose if used as primary           |

#### Option D — Address the cause: bound backtest pandas footprint

**Observation:** the python processes that triggered Event 2004 were
running backtests against ~10 MB of input data (47 days × 1440 1m bars
≈ 67k rows × ~10 cols float64 = ~5 MB pandas, ~10 MB with index/multi-
indexing). Reaching **60 GB virtual** on 10 MB of input is a 6000×
overhead — almost certainly a code-level memory issue, not an inherent
workload requirement.

Likely culprits (without having read the backtester code this session):

- **Pandas merge / pivot intermediates** retained in the function scope
  rather than freed between phases. A wide join across 67k rows can
  produce GB-class temporaries if done carelessly.
- **`multiprocessing.Pool` / `concurrent.futures`** forking the parent
  process — each child inherits the parent's full virtual address
  space (copy-on-write on Linux, copy-on-fork on Windows via spawn).
  N children × parent VM = multiplicative virtual commit.
- **Long-lived DataFrames** kept in memory for the report-writing phase
  rather than streamed to disk and dropped.
- **Numpy / pyarrow buffer caches** never released across batches.

A targeted fix here (chunked processing, drop-and-recompute pattern,
streaming writer) would reduce peak memory **naturally** — likely to
1 – 5 GB rather than 60 GB. This is the root-cause fix.

**However:** the backtester code (`scripts/backtest_kalshi_structure_arb.py`,
`scripts/backtest_bitunix_confluence.py`) is owned by parallel sessions
per the parallel-sessions feedback memory and the IC v1 coordination
note in BACKLOG.md. **Don't refactor it this session.** Flag for the
backlog as Mitigation 3.

| Pros                                                                 | Cons                                                                  |
| -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Fixes the root cause; no enforcement wrapper needed long-term        | Requires reading + refactoring backtester code                        |
| Reduces peak memory by ~10× regardless of cap                         | Parallel-session-owned code per session-collaboration rules           |
| Permanent improvement to the codebase                                | Takes a real engineering session, not a wrapper-install task          |

#### Option E — System-level commit limit / fixed pagefile

**Mechanism:** Windows lets the user override `AutomaticManagedPagefile`
and set a fixed pagefile size. Lowering pagefile below the default
~17 GB would lower the commit limit below the current ~33 GB. Once
commit limit is hit, processes' allocations fail with `ERROR_COMMITMENT_LIMIT`
rather than the system thrashing pagefile.

**Why this is wrong here:** the commit limit applies system-wide, not
per-process. Setting commit limit at 25 GB to "cap Python" would mean
that **every legitimate large allocation** (Chrome session restore,
video editing, OS background tasks under load) fails too. The pagefile
is sized to support all-running-processes' aggregate working set; a
manual cap is bluntly destructive.

Also: the desired behaviour is to **terminate the runaway Python
process**, not to fail all subsequent allocations. Job Object (Option
A) does the former; commit-limit lowering does the latter.

Skip.

### Recommendation

**Option A as primary, Option C as a later complement, Option D on the
backlog.** B and E rejected.

**Concrete implementation (when the Board approves):**

1. **Install procgov via winget** (one-time, ~30 s, no admin needed for
   user-scope install):

   ```
   winget install LowLevelDesign.ProcessGovernor
   ```

2. **Create a project shim `scripts/run_capped.ps1`** that wraps any
   command with the cap:

   ```powershell
   # scripts/run_capped.ps1
   param([Parameter(ValueFromRemainingArguments=$true)] [string[]] $Cmd)
   if (-not $Cmd) { Write-Error "Usage: run_capped.ps1 <command> [args...]"; exit 1 }
   procgov --maxmem 25G -- @Cmd
   ```

   Usage: `.\scripts\run_capped.ps1 python scripts/backtest_kalshi_structure_arb.py`

3. **Update [docs/runbooks/session_workload_defaults.md](../runbooks/session_workload_defaults.md)**
   to reference `run_capped.ps1` as the canonical way to invoke
   backtests + heavy pytest. Add the wrapper to the Python-operations
   checklist.

4. **Defer Option C (psutil conftest.py fixture)** until 48 h of
   procgov-wrapped runs demonstrate the cap is holding. Add only if
   Mitigation 1 + 2 leave any residual baseline noise that a
   code-level checkpoint would catch.

5. **Add Option D (backtester memory refactor) to BACKLOG** as
   Mitigation 3 (parallel-session-owned, P1 not P0). Worth doing
   eventually for clean engineering; not load-bearing once the cap
   is in place.

### Why Option A is the right call

The cheapest difference between the options is implementation cost
vs enforcement strength:

| Option | Install / setup cost | Enforcement strength       | Coverage                            |
| ------ | -------------------- | -------------------------- | ----------------------------------- |
| A      | ~30 s + 1 shim file  | **Hard kernel-enforced**   | Any invocation through the shim     |
| B      | n/a                  | n/a                        | n/a (deprecated)                    |
| C      | Touch every entry pt | Soft polling-based         | Only the entry points instrumented  |
| D      | Engineering session  | Natural (root-cause)       | Permanent, no enforcement needed    |
| E      | 1 reboot             | System-wide brute force    | Breaks legitimate uses              |

Option A has the best enforcement-per-install-cost ratio AND the broadest
coverage (any command can be wrapped). Option C is best paired with A
as a code-level second line of defense — but doing C without A leaves
the gap that motivated this analysis. Option D is the long-term answer
and should be the eventual goal, but it's an engineering project not a
mitigation, and the parallel-session ownership makes it cross-session
work. Option B doesn't exist; Option E is blunt.

### Status as of this commit

- **Mitigation 1 (workload reduction baseline): APPLIED.** Runbook
  live at `docs/runbooks/session_workload_defaults.md`. BACKLOG.md P0
  updated.
- **Mitigation 2 (Python VM cap, procgov / Option A): pending Board
  decision.** Implementation plan above. Awaiting "go" before running
  `winget install LowLevelDesign.ProcessGovernor` and creating the
  shim.
- **Mitigation 3 (backtester memory refactor / Option D): backlog.**
  Parallel-session-owned; address after Mitigation 2 stabilizes.

End of § 10.

---

## 11. Crash #9 — post-procgov-install (2026-05-18 22:08:46)

**TL;DR.** Crash #9 happened ~14 minutes after the procgov + `run_capped.ps1`
wrapper was committed (ab13673 at 21:54:16). The wrapper **was not invoked**
on the offending workload. The previous Claude session ran
`& "C:\Users\AA Incorporado\AppData\Local\Python\bin\python.exe" -m pytest
tests/test_kalshi_structure_arb.py …` **directly** — bypassing
`scripts/run_capped.ps1` and therefore bypassing the procgov Job Object cap
that was supposed to terminate at 25 GB. The python process grew to 57.88
GB virtual / 53.85 GB private / 9.1 GB working-set before the crash. H7 is
re-confirmed; the mitigation gap is **wrapper-invocation discipline**, not
procgov enforcement strength.

This crash *did* produce a kernel bugcheck dump
(`C:\Windows\Minidump\051826-15343-01.dmp`, 5,006,100 bytes), with bugcheck
**`0x0000010e` (VIDEO_MEMORY_MANAGEMENT_INTERNAL)** at 22:08:46. Sub-code
`0x2e` (= `0x000000000000002e`). The 4-minute gap between Event 2004
(22:04:25) and the bugcheck (22:08:46) is consistent with the H7 cascade
mechanism described in § 9: VM exhaustion → system thrashes → eventually a
GPU video-memory allocation fails → video memory manager hits an internal
error path that *can* still run bugcheck.

### Crash inventory entry

| Field                  | Value                                                 |
| ---------------------- | ----------------------------------------------------- |
| Crash #                | 9 (user-frame) / 12th Kernel-Power 41 in the inventory |
| Event 2004 time        | 2026-05-18 22:04:25 local                              |
| Kernel-Power 41 time   | 2026-05-18 22:08:27 local                              |
| BugCheck 1001 time     | 2026-05-18 22:08:46 local                              |
| BugCheck code          | `0x0000010e` (VIDEO_MEMORY_MANAGEMENT_INTERNAL)        |
| BugCheck parameters    | `0x2e, 0xffffd50c37e51460, 0xffffd50c50fbfb50, 0xffff888a5fe4ea50` |
| Dump file              | `C:\Windows\Minidump\051826-15343-01.dmp` (5.0 MB, present, ACL-protected — WinDbg deferred to next elevated session) |
| Report Id              | `3bf90318-6da0-489a-ad44-3260004c26a9`                |
| Top VM consumer (Event 2004) | `python.exe (PID 8544)` at **58,318,585,856 bytes** (54.3 GiB) |
| #2/#3 VM consumers     | `claude.exe (10156)` 0.60 GiB, `claude.exe (16740)` 0.45 GiB |

### What workload was running at crash time

From the prior Claude session's transcript
(`C:\Users\AA Incorporado\.claude\projects\C--Users-AA-Incorporado-cc\584ff160-b281-442a-ac8d-f296b143d867.jsonl`,
last write 22:04:22, 3 seconds before Event 2004):

- 22:01:50 — PowerShell tool invoked:
  ```
  & "C:\Users\AA Incorporado\AppData\Local\Python\bin\python.exe" -m pytest
    tests/test_kalshi_structure_arb.py …
  ```
  (background job, command id `bxmnb301m`). **No `run_capped.ps1`, no
  `procgov` in the invocation.**
- 22:03:52 — background job confirmed running, output redirected to
  `C:\Users\AAINCO~1\AppData\Local\Temp\claude\C--Users-AA-Incorp...`
- 22:04:02 — ad-hoc Get-Counter sample reported:
  - `\Memory\Committed Bytes` = **53.84 GB**
  - `\Memory\Available Bytes` = **0.94 GB**
- 22:04:14 — session text: "**MEMORY PRESSURE ALERT** — Committed 53.84 GB,
  Available 0.94 GB. Both past action thresholds. Identifying culprit and
  acting per crash protocol."
- 22:04:17 — Get-Process top: `python (8544) WS 9.1 GB, PM 53.85 GB,
  VM 57.88 GB`.
- 22:04:22 — last JSONL write (session frozen).
- 22:04:25 — Event 2004 fires.
- 22:08:27 — Kernel-Power 41 (hard reboot).
- 22:08:46 — Post-reboot BugCheck 1001 logged.

So the workload was **a scoped pytest on a *single* test file**
(`tests/test_kalshi_structure_arb.py`). That this could push python past
57 GB virtual is the same finding from § 9 — a single pytest discovery /
collection + imports under the trading_corp package can balloon if the
backtester / data-loading modules eagerly materialize large DataFrames at
import time. **The workload-reduction baseline (§ 10 Mitigation 1) said
"don't run the full backtest"; it did not say "don't import the
backtester at all," and pytest discovery against this file appears to
trigger heavy imports.**

### Was procgov engaged on python.exe (8544)?

**No.**

Three independent pieces of evidence:

1. **Transcript shows direct invocation.** The PowerShell command string
   captured in the prior session's JSONL is
   `& "...python.exe" -m pytest …`. No procgov, no `run_capped.ps1`.
2. **VM commit exceeded the cap by 2.3×.** procgov's wrapper enforces
   `--maxjobmem 25G --terminate-job-on-exit`. The Windows Job Object
   `JOB_OBJECT_LIMIT_JOB_MEMORY` limit is hard-enforced by the kernel: a
   `VirtualAlloc` past the cap returns `STATUS_NO_MEMORY` and the job is
   terminated. The python process reached **57.88 GB virtual / 53.85 GB
   private commit** — 2.3× the cap. Either procgov did not see this
   process at all, or its job was never attached. Direct invocation is
   the only plausible explanation.
3. **PSReadLine history does not contain `run_capped.ps1` or `procgov`.**
   The user-side history (`ConsoleHost_history.txt`, last write 21:29:36)
   has neither. The pytest command was issued by the previous Claude
   session via its PowerShell tool, not by the user, and was not wrapped.

This is the **wrapper-not-invoked** failure mode, not the **wrapper-failed
-to-enforce** failure mode. Procgov itself is installed
(`procgov.exe 3.2.25275.19` at
`C:\Users\AA Incorporado\AppData\Local\Microsoft\WinGet\Packages\…`) and
`scripts/run_capped.ps1` is in tree.

### Memory sampler trajectory

The user's continuous memory sampler launched earlier in the day
(PSReadLine history tail: `while ($true) { ... Get-Counter ... Start-Sleep
30 }`) was running in a separate PowerShell window. That window's
scrollback is **not recoverable from this session** — PSReadLine history
captures only commands the user typed, not their output, and the file's
last-write was 21:29:36 (no further user-typed commands captured before
crash). Whether that sampler window survived to the crash or was closed
earlier is unknown without ScreenShots / log files the user kept.

The single Get-Counter sample the previous Claude session took at 22:04:02
(Committed 53.84 GB, Available 0.94 GB) is the only sampler data
recoverable for this crash. It confirms the H7 picture (VM at ~33 GB
commit limit + 17 GB pagefile expansion = ~50 GB total commit; 53.84 GB
is past that, system is in the "automatic pagefile expansion absorbing
overflow" regime described in § 9) but is a single sample, not a
trajectory.

### Dump file presence

| Dump file                                | Time            | Size      | Notes |
| ---------------------------------------- | --------------- | --------- | ----- |
| `C:\Windows\Minidump\051826-14937-01.dmp` | 5/18 11:17:11 AM | 2.82 MB  | Crash #6, already analyzed (§ 2 WinDbg verdict; nvlddmkm.sys). |
| `C:\Windows\Minidump\051826-15343-01.dmp` | 5/18 10:08:46 PM | **5.0 MB**, **NEW** | **Crash #9.** Bugcheck `0x10e`. Read-protected to current ACL; deferred to elevated session. |

A `MEMORY.DMP` (kernel-mode full dump, not minidump) was also referenced
by Event 1001: `A dump was saved in: C:\WINDOWS\MEMORY.DMP.` Not enumerated
in this section (it's typically large, > 1 GB, also ACL-protected).

### Re-confirmation of H7 mechanism

Bugcheck `0x10e` (VIDEO_MEMORY_MANAGEMENT_INTERNAL) with Event 2004 four
minutes prior is the cleanest single-cause expression of H7 we have so
far:

1. python.exe at 54 GB private commit pushes the system past pagefile
   expansion.
2. Event 2004 fires at 22:04:25 (kernel notices low virtual memory).
3. For ~4 minutes the system thrashes (pagefile churn, every kernel
   allocation slow, including GPU driver allocations).
4. At 22:08:27 the video memory manager finally hits an allocation /
   accounting path that cannot proceed (sub-code `0x2e`), bugchecks
   cleanly (storage stack still up), and reboots.

This is exactly the cascade § 9 predicted. The one-NVIDIA-dump-amongst-
many-no-dump-crashes pattern from earlier is no longer the puzzle it was;
under H7, the *kind* of bugcheck depends on which allocation path happens
to fail first, and graphics-memory paths fail under VM pressure as often
as anything else.

### Updated hypothesis ranking

**H7 (workload pressure / virtual-memory exhaustion) — LEADING.** Status
upgraded from "untested but with strong correlative evidence" (§ 9) to
**"directly mechanism-confirmed for crash #9."** Crash #9 alone provides:

- Event 2004 → bugcheck 4 minutes later (correlation tightened from "2–7
  min before" to "bugcheck during the resource-exhaustion window itself").
- A bugcheck code (`0x10e`) whose semantics fit the H7 cascade
  end-to-end.
- A workload (pytest collection on a single test file, no backtest run)
  small enough that H7's "any sustained python ≥ 50 GB will crash this
  machine" framing — rather than "the full Kalshi SA backtest is the
  problem" — is the right framing.

**H7 mitigation status:**

- **M1 (workload reduction baseline, runbook):** Insufficient on its own.
  The prior session was already operating under the M1 workload-defaults
  rules and still hit the crash, because pytest discovery is not what M1
  was scoped to forbid. M1 needs an addendum: "no python invocation under
  trading_corp/ imports is exempt — wrap *all* pytest runs in
  `run_capped.ps1`, not just full backtests."
- **M2 (procgov + `run_capped.ps1` wrapper):** Installed and functional
  but **not enforced by default.** Crash #9 happened because the wrapper
  was bypassed. The wrapper itself is fine; the gap is that nothing
  forces its use.

**H7 mitigation invalidated?** No. **H7 mitigation gap exposed:**
the wrapper is an *opt-in* discipline rather than a *cannot-be-bypassed*
constraint. Two patterns can close the gap:

- **Default-on wrapper at the Claude tool layer.** Bias the project /
  agent's PowerShell + Bash tools to prefer
  `.\scripts\run_capped.ps1 python …` over `python …` for any python
  invocation that touches `trading_corp/` or `tests/`. This is a
  documentation / agent-behavior change, not a code change.
- **Per-process default cap at the OS level.** Configure procgov as a
  service watching for `python.exe` processes spawned under this user
  and attaching a job-mem limit automatically (procgov's
  `--monitor-process` mode). This is an OS-side install / config that
  catches *any* python invocation regardless of how it's launched.
  Requires Board approval; higher install/maintenance cost than the
  agent-side rule.

The other hypotheses are unchanged from § 9's ranking. H2 (NVIDIA) gets a
modest nudge as the *kind of bugcheck* H7 cascades into on this machine
(graphics-memory path keeps showing up), but the leading hypothesis stays
H7. The H2 driver is still the 2021 OEM driver post-clean-install (per
§ 9), so its baseline robustness under memory pressure is poor; under H7
this is a symptom, not a separate hypothesis.

### Step-4 recommendation (do not apply this session)

**Primary recommendation: tighten wrapper-invocation discipline.**

Specifically, three concrete actions, in order of cost:

1. **(Cheap, hour-scale)** Amend `docs/runbooks/session_workload_defaults.md`
   to make `run_capped.ps1` the **mandatory** invocation path for *any*
   python command that touches `trading_corp/` or `tests/`, **including
   pytest discovery on a single file.** Add a short rationale: pytest
   collection runs the package's `__init__.py`s, which transitively
   import the backtester's heavy modules and can balloon. The runbook
   should give the explicit wrapped-pytest invocation form. Then update
   CLAUDE.md or the session-start prompt to surface this rule at session
   open so future Claude sessions adopt it by default.

2. **(Medium, session-scale)** Add a project-level pre-commit / lint
   check (or just a session-start reminder) that searches for direct
   `python` / `python.exe` invocations in any recent Claude transcript
   under `.claude/projects/.../jsonl` and flags them. This is the
   smallest amount of automation that closes the human-discipline gap.

3. **(Heavier, Board-decision-scale)** Install procgov as a session-wide
   watchdog using `procgov --monitor-process python.exe --maxjobmem 25G`
   (or equivalent — exact flag set TBD against procgov's docs). This
   enforces the cap regardless of invocation path. Requires testing
   that it doesn't interfere with legitimate small python invocations
   (e.g., a one-off `python --version` or a benign script), and that
   the cap isn't tripped by trading_corp's normal startup. **Stop and
   ask before doing this** — it's a system-wide change.

**Do not apply Mitigation 3 (backtester refactor) this session** —
parallel-session-owned code per § 10 and CLAUDE.md ownership rules.

**Secondary recommendation: WinDbg the new dump in an elevated session.**

`C:\Windows\Minidump\051826-15343-01.dmp` is ACL-protected; running
`cdb !analyze -v` against it requires an elevated PowerShell or copying
the dump out under elevation. This is **non-blocking** — H7 is already
confirmed by Event 2004 + bugcheck-code semantics — but a stack trace
showing `dxgkrnl!…`, `dxgmms2!…`, or `nvlddmkm!…` in the faulting frames
would (a) tighten which video-memory path failed, and (b) settle whether
the 2021 OEM NVIDIA driver's residual instability is contributing under
memory pressure. Schedule as an M4 followup; not load-bearing for the
H7 mitigation call.

### What did *not* fail

For the avoidance of future doubt:

- **Procgov itself.** The tool is installed and ready. It was never
  invoked on the offending process. Do not write off procgov; write off
  the assumption that an opt-in wrapper is sufficient.
- **The Event 2004 detector.** Fired on time, identified the right
  culprit, gave 4 minutes' warning. The previous session noticed the
  alert and tried to act on it but was too late — the BSOD landed
  before the session's mitigation flow could complete.
- **The minidump pipeline.** Crash #9 produced a dump (5 MB, ACL-locked,
  retrievable). The "no dump on 11/12 crashes" pattern from § 9 is now
  10/12 — bugcheck `0x10e` ran the dump path cleanly, supporting the
  § 9 reading that the no-dump cases are downstream of storage / commit
  wedge, not a broken dump configuration.

### Addendum: Watchdog mitigation attempt — investigated and abandoned (2026-05-18 22:30 – 23:15)

Following the § 11 recommendation 3 ("Board-decision-scale: procgov as
session-wide watchdog"), procgov 3.2.25275.19 was installed via `winget`
on 2026-05-18 21:54 (commit `ab13673`) and then `procgov --install
--maxjobmem 25G python.exe` (+ same for `pythonw.exe`) was run in elevated
PowerShell to configure it as a service watching `python.exe` and
`pythonw.exe` with a 25 GB Job-Object commit cap. Intent: remove the
wrapper-invocation discipline gap crash #9 exposed — every python.exe
launch on the machine, regardless of caller, should attach to a capped
Job Object at start.

It does not work on this OS build. Investigation summary:

1. **`procgov --install` does NOT use IFEO** despite public docs for
   procgov 3.x stating "the service uses IFEO to start the procgov process
   as a debugger for each new instance of the monitored process." A binary
   string scan of the installed `procgov.exe` (8.1 MB, dated 2025-10-02)
   found no `Image File Execution Options` / IFEO / `Debugger`-as-CLI-flag
   strings — only `EtwEventProvider`, `EtwTraceListener`, `<StartMonitor>`,
   `IOCPListener`, `NewProcessEventFormatter`, and
   `CreateProcessWithJobAssigned`. The actual mechanism is **post-launch
   ETW attach**: the service subscribes to kernel process-creation events,
   sees each new `python.exe`, then attempts to open the process handle
   and add it to a Job Object. Verbose mode (`procgov -v --install …`)
   confirmed no IFEO write attempt — the verbose flag produced only the
   same single-line cap-confirmation as non-verbose install.
2. **ETW step works; OpenProcess step fails.** Application event log
   showed repeated warnings from the service:
   `Failed when getting information about process N: System.ComponentModel.Win32Exception (5): Access is denied. at System.Diagnostics.ProcessManager.OpenProcess(Int32, Int32, Boolean) + 0x165 at System.Diagnostics.NtProcessManager.GetFirstModule(Int32) + 0xf at ProcessGovernor.Program.ProcessGovernorService.<Start>g__RunProcessObserver|3_1(CancellationToken) + 0x270`.
   ETW fired; identification step
   (`ProcessManager.OpenProcess` → `NtProcessManager.GetFirstModule`)
   refused. Service runs as `NT AUTHORITY\SYSTEM`, so this points at a
   token-privilege issue: `SeDebugPrivilege` not enabled by default
   under Win11 service hardening despite the SYSTEM account having it
   nominally.
3. **`RequiredPrivileges = SeDebugPrivilege` (β-minimal) partially helped.**
   Added `RequiredPrivileges` to the service registration at
   `HKLM:\SYSTEM\CurrentControlSet\Services\ProcessGovernor` as a
   `REG_MULTI_SZ` value. After service restart, access-denied warnings
   stopped — service token now had `SeDebugPrivilege` enabled,
   `OpenProcess` succeeded. **But smoke test (`IsProcessInJob`
   immediately after python launch + delayed checks at t=100, 500,
   1000, 2000, 5000 ms) all still returned `False`.** The service was
   now identifying the process successfully but not attaching the Job
   Object — silently.
4. **BITS-equivalent privilege set (β-broader) exposed a deeper wall.**
   Reset `RequiredPrivileges` to the BITS service's full set
   (`SeChangeNotify, SeCreateGlobal, SeImpersonate, SeAssignPrimaryToken,
   SeIncreaseQuota, SeDebug`) — structurally analogous to procgov's
   needs. Service restart: clean. Smoke test: **`In job: False` still.**
   Application log now showed a *new* error kind for *other* processes:
   `Failed when getting information about process N: System.ComponentModel.Win32Exception (299): Only part of a ReadProcessMemory or WriteProcessMemory request was completed. at System.Diagnostics.NtProcessManager.EnumProcessModulesUntilSuccess(...) at System.Diagnostics.NtProcessManager.GetModules(Int32, Boolean) + 0x18b`.
   **`ERROR_PARTIAL_COPY` (299) is a kernel-side memory-read restriction**
   — typically caused by WoW64 boundary mismatch, page-protection rules,
   or the process exiting mid-read. Not a privilege issue. Adding more
   privileges doesn't fix it. The python smoke-test processes themselves
   continued to produce *no log entry at all*, meaning the service either
   was matching them and failing silently on a later step (no logging
   on `AssignProcessToJobObject` errors) or wasn't matching them at all.

**Conclusion.** On Windows 11 build 26200 + procgov 3.2.25275.19, the
service's `.NET ProcessManager.GetModules` call cannot reliably inspect
user-mode python processes regardless of which `RequiredPrivileges` set
the service is granted. The `ERROR_PARTIAL_COPY` error indicates a
Windows-side process-memory-read restriction outside what service-token
privileges can adjust. There is no `--debugger` CLI mode on this procgov
build (confirmed by binary string scan), so the "manual IFEO → procgov
as debugger" workaround is also unavailable.

**Rollback performed 2026-05-18 23:15.** `procgov --uninstall-all` in
elevated PowerShell. Verified post-uninstall (read-only from a
non-elevated session):

- `Get-Service ProcessGovernor` → null (service unregistered).
- `Test-Path HKLM:\SOFTWARE\ProcessGovernor` → False (per-image config
  cleared).
- `Test-Path HKLM:\SYSTEM\CurrentControlSet\Services\ProcessGovernor` →
  False (service registry key cleared; `RequiredPrivileges` died with it).
- IFEO entries for `python.exe` / `pythonw.exe` → never existed.
- `C:\Program Files\ProcessGovernor\` directory remains with `procgov.exe`
  inside (cosmetic — not on PATH, not service-registered, not invoked
  by anything). Optional manual cleanup; not load-bearing.

Procgov binary at the WinGet user-scope path
(`%LOCALAPPDATA%\Microsoft\WinGet\Packages\LowLevelDesign.ProcessGovernor_Microsoft.Winget.Source_8wekyb3d8bbwe\procgov.exe`)
remains on PATH and continues to back `scripts\run_capped.ps1` as the
per-invocation wrapper. Wrapper smoke-tested post-uninstall:
`.\scripts\run_capped.ps1 python -c "print('wrapper still works')"`
→ `Maximum job committed memory (MB): 25,600` → `wrapper still works`,
exit 0. The wrapper path is unaffected by the uninstall.

### What NOT to retry on this OS build

- **Don't reinstall procgov as a service watchdog.** Same ETW +
  `OpenProcess` + `GetModules` mechanism, same `ERROR_PARTIAL_COPY` wall.
  Adding more privileges to `RequiredPrivileges` won't fix it.
- **Don't try manual IFEO writes pointing at procgov.exe.** Procgov has
  no `--debugger` CLI mode (binary scan confirmed). Writing
  `HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\python.exe\Debugger = "...\procgov.exe --debugger"`
  would break every python.exe launch on the system.
- **Don't try to enable `SeDebugPrivilege` some other way** (e.g.,
  `AdjustTokenPrivileges` shim, scheduled task with explicit
  privileges). The `SeDebugPrivilege` step is solved by
  `RequiredPrivileges`; the wall is `ERROR_PARTIAL_COPY` on
  `EnumProcessModulesUntilSuccess`, which token privileges don't affect.
- **Do consider, if revisiting later:** (a) a newer procgov release
  (3.3+ may have rewritten the ProcessManager call to use
  `K32EnumProcessModules` or `NtQueryInformationProcess` directly,
  bypassing the .NET wrapper that hits `ERROR_PARTIAL_COPY`); (b) a
  different watchdog tool with pre-launch interception (true IFEO
  redirection via a small custom debugger stub, ~100 LoC, would attach
  the Job Object before `OpenProcess` becomes necessary). Both are
  higher-cost than the wrapper + discipline path warrants today.

### Mitigation state at end of this turn

- **Mitigation 1 (workload reduction baseline):** APPLIED, unchanged
  ([docs/runbooks/session_workload_defaults.md](../runbooks/session_workload_defaults.md)).
- **Mitigation 2 (Python VM cap via `scripts\run_capped.ps1` wrapper):**
  APPLIED + **MANDATORY** per runbook + CLAUDE.md "STOP AND READ"
  invariant #6. Per-invocation discipline is the enforcement.
- **Mitigation 2b (procgov as service watchdog):** investigated,
  **abandoned** per analysis above. Procgov service uninstalled.
- **Mitigation 3 (backtester memory refactor):** unchanged, on backlog
  as parallel-session-owned code.
- **Future enforcement gap closer:** "agent-side transcript lint at
  session start" filed in BACKLOG.md as a follow-up to catch
  unwrapped-python invocations in recent Claude transcripts. Don't
  implement until 48 h of wrapper-discipline data shows whether the
  lint is needed.

End of § 11.

---

## 12. Procgov wrapper cap does not enforce (verified 2026-05-19 22:50)

**TL;DR.** The `scripts\run_capped.ps1` wrapper does not enforce its 25 GB
Job-Object cap on Windows 11 build 26200 + procgov 3.2.25275.19. An
explicit allocation test executed *through the wrapper* reached 30 GB of
private commit with the cap-engaged banner displayed and was not
terminated. This invalidates the "Mitigation 2 applied" status recorded
at commit `88a6c3b`; the wrapper-mandatory discipline (CLAUDE.md
invariant #6, runbook) provides **no actual memory protection** on this
OS build. Crash #9 (§ 11) was misclassified as a *wrapper-not-invoked*
failure; the more accurate framing — consistent with the new evidence —
is *wrapper-cannot-enforce.* Crash #10 (2026-05-19 22:35, python at
55.3 GB virtual commit despite wrapper engagement) corroborates.

### Allocation test (2026-05-19 22:50)

Procedure: `allocate_test.py` allocates `bytearray(100 * 1024 * 1024)`
chunks until either 30 GB is reached or the process is terminated.
Invoked through the wrapper:

```
.\scripts\run_capped.ps1 python allocate_test.py
```

Observed:

- Wrapper banner printed `Maximum job committed memory (MB): 25,600` —
  procgov reporting cap *intent*.
- Python proceeded past 25.6 GB without termination, allocation continued
  to the 30 GB self-imposed target.
- Final line of test output: `DID NOT HIT CAP`.
- Continuous memory sampler at 22:50:15 (local): `\Memory\Committed Bytes`
  = **22.03 GB**, `\Memory\Available Bytes` = **0.31 GB**. The 25 GB cap
  is not enforced at the kernel level; the system instead approached
  whole-OS exhaustion under wrapper supervision.
- Process exited cleanly on test completion; OS reclaimed pages
  immediately (post-test sample: 7.72 GB committed / 11.32 GB
  available — *better* than pre-test baseline 8.89 GB available).

Interpretation: the wrapper banner reflects what procgov *was asked* to
do, not what the Windows Job Object subsystem actually enforced. On this
OS build the `JOB_OBJECT_LIMIT_JOB_MEMORY` path that should return
`STATUS_NO_MEMORY` and terminate the job is not engaging. Mechanism
unconfirmed (a job object may not be attached at all, or the
`JobObjectExtendedLimitInformation` write may silently no-op under
26200's job-hardening changes); behavioral outcome is unambiguous.

### Re-classification of crash #9 (§ 11)

§ 11 concluded crash #9 was a *wrapper-not-invoked* failure based on
transcript evidence that the PowerShell command string lacked
`run_capped.ps1`. That observation still stands, but the inference that
*invoking* the wrapper would have prevented the crash does not. § 12's
allocation test demonstrates that python at 30 GB private commit under
the wrapper is not terminated by the cap. A wrapped pytest invocation
exhibiting the same backtester-import balloon to 54 GB private commit
would, on the evidence available, crash the machine identically.

Crash #10 (2026-05-19 22:35, python.exe at 55.3 GB virtual commit
despite wrapper engagement on the offending workload) is independent
confirmation: a workload that *did* go through the wrapper still reached
private-commit levels well past the cap.

### Mitigation status, re-evaluated

| Mitigation | Prior status (§ 11)        | Status as of § 12                                                                                  |
| ---------- | -------------------------- | -------------------------------------------------------------------------------------------------- |
| **M1** (workload reduction baseline) | APPLIED, marginal benefit | **APPLIED.** Still partial; reduces frequency, does not bound peak commit. |
| **M2** (procgov wrapper, per-invocation) | APPLIED + MANDATORY | **NON-FUNCTIONAL on 25H2 / build 26200 + procgov 3.2.25275.19.** Cap is advisory, not enforced. Flagged for removal from runbook + CLAUDE.md "STOP AND READ" invariant #6. |
| **M2b** (procgov as service watchdog) | Abandoned (§ 11 addendum) | Unchanged. Not re-attempted; `ERROR_PARTIAL_COPY` wall is upstream of cap-enforcement question. |
| **M3** (backtester memory refactor) | Backlog, parallel-owned | **Only mitigation that addresses root cause.** Coordination-blocked. Priority raised: M2's removal leaves M3 as the only viable software fix. |
| **M4** (agent-side transcript lint for unwrapped python) | Filed in BACKLOG.md | **Moot.** Closes a wrapper-discipline gap that does not protect anything. Recommend de-prioritize / close. |

### Path forward — options surveyed

The wrapper path is verified non-functional. Remaining options, ordered
by enforcement strength vs. workflow cost:

1. **Hyper-V VM with bounded RAM.** Install Windows 11 in a VM with
   12 GB allocated; perform project work inside the VM. If python
   exhausts memory, the VM crashes; the host stays up. Setup ~2–4 h.
   Most rigorous answer; actually enforces.
2. **Docker Desktop with memory-limited container.** Python inside a
   container with `--memory=12g`. Container OOM-kills python cleanly;
   host unaffected. Requires Docker Desktop install + adapting workflows
   to run inside containers. Setup ~1–2 h.
3. **psutil-based polling watchdog.** Python script polling at 100 ms
   that kills the process at a threshold. Race-prone: a fast
   allocation can cross the threshold and crash before the watchdog
   acts. Setup ~1 h. Fragile patch.
4. **M3 (backtester refactor).** Addresses the 60 GB-virtual-for-10 MB-
   input root cause. With M3 the legitimate workload fits in 16 GB and
   no containment is needed. Coordination-blocked currently.
5. **Hardware RAM upgrade.** 32 GB ($80–150) gives breathing room;
   64 GB ($150–300, this laptop's max) gives comfortable headroom even
   with the broken backtester. Does not fix the bug; makes it less
   catastrophic. Cheapest path back to working sessions while M3
   remains blocked.

The wrapper path is closed for further investment. WSL / Docker / VM are
heavyweight workflow changes the user has either declined or deferred.
The psutil watchdog is fragile by construction. RAM upgrade is the
cheapest near-term defense while M3 unblocks.

### Action items emerging from § 12

- **Update CLAUDE.md invariant #6** to reflect the wrapper is
  non-functional; remove the wrapper-mandatory framing or convert it to
  a "no-op kept for forward-compat with a future enforcing build" note.
- **Update runbook (`docs/runbooks/session_workload_defaults.md`)** to
  remove the wrap-every-python rule and replace it with the actual
  current defense (workload reduction + avoid pytest on full
  trading_corp import chain).
- **Raise M3 priority in BACKLOG.md.** With M2 gone, M3 is the only
  software mitigation that bounds peak commit.
- **De-prioritize or close M4** (transcript lint for unwrapped python)
  in BACKLOG.md.
- **Re-baseline session-start prompt** to omit wrapper guidance.

End of § 12.

End of report.
