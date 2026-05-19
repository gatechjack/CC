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
- [ ] **Backtests: MANDATORY via the capped wrapper.**

      ```powershell
      .\scripts\run_capped.ps1 python scripts\backtest_kalshi_structure_arb.py [args...]
      .\scripts\run_capped.ps1 python scripts\backtest_bitunix_confluence.py [args...]
      ```

      The wrapper applies a 25 GB Windows-Job-Object commit-charge cap
      to the entire process tree (parent + children). If the cap is
      reached, the kernel rejects further `VirtualAlloc` calls (python
      raises `MemoryError`) — no thrash, no Event 2004, no Kernel-Power
      41 crash. See § 10 of the diagnostic report for the mechanism
      details.
- [ ] **Pytest: MANDATORY via wrapper. No exemption for single-file
      runs, no exemption for "small" test sets.**

      ```powershell
      .\scripts\run_capped.ps1 python -m pytest tests/<file>.py [args...]
      .\scripts\run_capped.ps1 python -m pytest tests/ [args...]
      ```

      Applies to single-file pytest, scoped pytest, and full-suite
      pytest equally. **Crash #9 (2026-05-18 22:08) was an unwrapped
      pytest on a *single* test file (`tests/test_kalshi_structure_arb.py`)
      that grew to 58 GB virtual commit and BSOD-ed the machine.**
      The prior version of this runbook exempted "the 78 Branch A
      baseline tests" because they finished in 0.4 s; that exemption
      is removed. Wall-time and apparent test size are not reliable
      predictors of memory footprint — pytest discovery transitively
      imports the package's `__init__.py`s and trading_corp's import
      chain can balloon. See § 11 of the diagnostic report for the
      crash #9 forensics and the watchdog-mitigation attempt that was
      abandoned.
- [ ] **Trivial sanity checks: may run unwrapped.**

      ```powershell
      python --version
      python -c "print('hi')"
      python -c "import ctypes; ..."   # one-liners with no trading_corp/tests imports
      ```

      Bound: zero `trading_corp` / `tests/` imports, no `pandas` /
      `numpy` load. If a script imports anything from this project,
      it goes through the wrapper.
- [ ] **One backtester at a time.** Heavy backtests (BitUnix v3 hybrid,
      Kalshi structure-arb) routinely reach 45 – 60 GB virtual on this
      machine *unwrapped*. Even with the cap, concurrent runs share
      the 25 GB job budget — sequential, not parallel.
- [ ] **Watch the sampler climb.** If Committed climbs above 15 GB or
      Available drops below 1 GB during a run, **abort the run** (Ctrl-C
      or kill from Task Manager) before Event 2004 fires. The cap is
      the safety net; the sampler is the early warning.
- [ ] **Re-baseline between runs.** Don't keep large pandas DataFrames in
      memory across backtest invocations. Restart the Python process
      between backtest sweeps.

### Wrapper installation + smoke-test results (one-time, 2026-05-19)

```
winget install LowLevelDesign.ProcessGovernor    # procgov 3.2.25275 installed
.\scripts\run_capped.ps1 python -c "print('hello from wrapped python')"
  → Process Governor v3.2.25275.19 - sets limits on processes
  → Maximum job committed memory (MB):          25,600
  → All configured limits will also apply to the child processes.
  → hello from wrapped python
  → exit code 0
```

If `procgov` is not on PATH in a fresh shell (winget noted "restart your
shell to use the new value"), open a new PowerShell window or refresh
PATH manually:

```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable('Path','User') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path','Machine')
```

---

## What this doesn't cover

This runbook + the `run_capped.ps1` wrapper together implement
**Mitigations 1 and 2** of the H7 response:

- **Mitigation 1 (workload reduction baseline)**: session hygiene
  (one Claude window, Discord closed, browser minimal, sampler
  running). The session-start checklist above.
- **Mitigation 2 (Python VM cap)**: 25 GB Job-Object commit cap via
  `procgov` and `scripts\run_capped.ps1`. The Python-operations
  checklist above. **The wrapper is MANDATORY (not recommended) for
  every python invocation that imports trading_corp/ or tests/.**
  Wrapper-invocation discipline is the only enforcement; no OS-level
  enforcement exists on this build (see Mitigation 2b below).
- **Mitigation 2b (OS-level watchdog via procgov service)**:
  investigated 2026-05-18, **abandoned**. Procgov's service mode
  cannot complete its Job-Object attach on Win11 26200 — the .NET
  `ProcessManager.GetModules` call hits `ERROR_PARTIAL_COPY` on
  `EnumProcessModulesUntilSuccess` regardless of `RequiredPrivileges`
  tuning. See [docs/diagnostics/2026-05-19_crash_diagnosis.md § 11](../diagnostics/2026-05-19_crash_diagnosis.md)
  for the full investigation. Don't reinstall procgov as a service
  on this OS build.

**Mitigation 3 (backtester memory refactor)** — investigation of why
backtesters reach 60 GB virtual on ~10 MB of input data — is on the
backlog as parallel-session-owned code. Address only after the
cap mechanism has demonstrated a 48 h crash-free window. See
[docs/diagnostics/2026-05-19_crash_diagnosis.md § 10](../diagnostics/2026-05-19_crash_diagnosis.md)
for full analysis.

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
