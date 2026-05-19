# Session workload defaults — H7 mitigation 1

Workload-reduction baseline that should be in place at session start.
Mitigates **H7 (workload pressure / VM exhaustion)** identified in
[docs/diagnostics/2026-05-19_crash_diagnosis.md § 9](../diagnostics/2026-05-19_crash_diagnosis.md).
Active from 2026-05-19; supersedes any prior informal workload guidance.

Why this matters: on this 16 GB RAM + 17 GB pagefile = ~33 GB commit-limit
system, the Event 2004 → Kernel-Power 41 crash chain fires when committed
virtual memory approaches the limit. 10/10 recent crashes were preceded by
Event 2004 within 2–7 minutes. The cause is heavy backtester Python
processes (45 – 60 GB virtual commit), amplified by baseline desktop load.
Keep committed memory below the action thresholds and the crash chain
doesn't start.

---

## Session-start checklist (30-second scan)

Before any session that will run Python:

- [ ] **One Claude desktop window only.** Multiple windows compound process
      overhead (~400 MB working set + ~3.5 GB virtual each, ×N).
- [ ] **Discord closed.** Discord routinely sits at 3 × 3.5 GB virtual = ~10 GB
      committed. Close it during sessions; reopen between.
- [ ] **Browser closed or minimal tabs.** Browsers are the largest
      discretionary memory consumer. If you need docs/dashboards open, use a
      single window with the minimum needed tabs and close on idle.
- [ ] **WSL not running.** WSL is currently not installed on this machine;
      `wsl --status` returns "not installed." If WSL gets installed later,
      default to `wsl --shutdown` at session start.
- [ ] **Memory sampler running** in a visible PowerShell window (command
      below). Check it every few minutes during Python work.
- [ ] **Committed at session start < 11 GB.** If higher, close more processes
      before starting Python work.

---

## Memory sampler

Run this in a visible PowerShell window at session start. Each line prints
`HH:MM:SS` plus `Committed Bytes` and `Available Bytes` in GB. Watch
committed climb during backtests.

```powershell
while ($true) {
  Get-Date -Format "HH:mm:ss"
  Get-Counter '\Memory\Committed Bytes', '\Memory\Available Bytes' -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty CounterSamples |
    Format-Table Path, @{N='GB';E={[math]::Round($_.CookedValue/1GB,2)}} -AutoSize
  Start-Sleep -Seconds 30
}
```

---

## Memory thresholds for action

The Event-2004 → crash chain fires when **committed memory** approaches
the commit limit (currently ~33 GB; auto-grows via pagefile but the
2004 alert fires well before the hard limit). Action at the lower
thresholds keeps the crash chain from starting.

| Committed Bytes        | Available Bytes       | Action                                                                                |
| ---------------------- | --------------------- | ------------------------------------------------------------------------------------- |
| < 11 GB                | > 4 GB                | **Safe session start.** Begin Python work.                                            |
| 11 – 12 GB             | 3 – 4 GB              | **Yellow.** Avoid launching new heavy processes. Finish current backtest, don't start another. |
| **> 12 GB**            | **< 3 GB**            | **Stop and reassess.** Identify what's running. Close discretionary processes.        |
| **> 14 GB**            | **< 1.5 GB**          | **Abort.** Kill the heaviest Python process from Task Manager BEFORE the crash hits. Do not "wait it out" — Event 2004 fires next, then K-P 41 within minutes. |

Use Task Manager (Ctrl+Shift+Esc) → Details tab → sort by Memory (private
working set), or PowerShell:

```powershell
Get-Process python -ErrorAction SilentlyContinue |
  Select-Object Id, ProcessName,
    @{N='WS_MB';E={[math]::Round($_.WorkingSet64/1MB,1)}},
    @{N='VM_GB';E={[math]::Round($_.VirtualMemorySize64/1GB,2)}} |
  Sort-Object VM_GB -Descending
```

Kill the worst offender: `Stop-Process -Id <pid> -Force`.

---

## Python operations checklist

When you do need to run Python:

- [ ] **Note current Committed before launching.** Have headroom of at
      least 4 GB on top of the baseline.
- [ ] **Subprocess-batched pytest only.** Run targeted test files, not
      `pytest tests/` full-suite. Pytest collection over a large suite
      can itself push committed by 1 – 2 GB before any test runs.
- [ ] **One backtester at a time.** Heavy backtests (BitUnix v3 hybrid,
      Kalshi structure-arb) routinely reach 45 – 60 GB virtual on this
      machine. Two concurrent runs is a guaranteed crash.
- [ ] **Watch the sampler climb.** If Committed climbs above 15 GB or
      Available drops below 1 GB during a run, **abort the run** (Ctrl-C
      or kill from Task Manager) before Event 2004 fires.
- [ ] **Re-baseline between runs.** Don't keep large pandas DataFrames in
      memory across backtest invocations. Restart the Python process
      between backtest sweeps.

---

## What this doesn't cover

This runbook is the **workload-reduction baseline (Mitigation 1)**. It
keeps memory pressure below the Event-2004 trigger zone through
disciplined session hygiene, not enforcement.

The complementary **Python VM cap (Mitigation 2)** — a hard per-process
limit that prevents a runaway Python from reaching 50+ GB even if the
user forgets to watch the sampler — is analyzed in
[docs/diagnostics/2026-05-19_crash_diagnosis.md § 10](../diagnostics/2026-05-19_crash_diagnosis.md)
and is **pending Board decision** as of this commit. When implemented,
the cap mechanism will be referenced from this runbook.

Investigation of why backtesters reach 60 GB virtual in the first place
(when input data is ~10 MB) is **Mitigation 3 (root cause)** — currently
on the backlog, parallel-session-owned code.

---

## Verification after applying this baseline

After 24 – 48 h of running with these defaults:

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,1001,6008,2004;
    StartTime=(Get-Date).AddHours(-48)} -ErrorAction SilentlyContinue |
    Select-Object TimeCreated, Id, ProviderName |
    Format-Table -AutoSize
```

- Empty output = workload-reduction baseline holding; H7 mitigation
  evidence strong.
- Any new Event 2004 = baseline insufficient; the cap mechanism
  (Mitigation 2) should be implemented next.
- Any new K-P 41 / 1001 / 6008 = H7 not the sole cause; reopen H1
  (Intel RST uninstall, M2 in § 3 of the diagnostic).
