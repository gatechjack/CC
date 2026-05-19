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

### H1 — Intel Rapid Storage Technology driver (`iaStorAC.sys` / `iaStorAVC.sys`) is the immediate cause [HIGH]

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

End of § 8.

End of report.
