# Next-session pickup prompt (2026-05-22) — IC v1 first paper-run day

*Written 2026-05-21 ~03:30 UTC at the end of the IC v1 first-prod-ship session.*

*This session shipped Iron Condor v1 end-to-end to prod. The first scan window for IC v1 fires today (Thursday 2026-05-21) at 09:45–09:50 ET = 13:45–13:50 UTC. By the time you read this in the next session, the first paper scan should have either fired or been skipped (weekend/holiday). Pick up by checking that state.*

---

Paste this into a fresh Claude Code session at `C:/Users/AA Incorporado/cc`:

---

Resuming after the 2026-05-21 03:09 UTC IC v1 first-prod-ship session. IC v1 is **live on prod in paper mode**, `auto_execute: false`. Read the **EOS snapshot at the top of `BACKLOG.md`** first — that's the canonical record of where this branch left off.

## Headlines from last session

- **IC v1 SHIPPED to prod 2026-05-21 03:09 UTC.** 30 files (3 commits' worth: commits A `365114b` + B `7c1eef0` + my wiring `65c8cdd`) via chunked az transport. Plus home tile fix `19b6dba` shipped separately at 03:22 UTC.
- **First-attempt crash loop at 02:10 UTC** because patch-only deploy didn't include commit A's 18 IC modules — `ModuleNotFoundError`. Rolled back at 02:17 UTC. New feedback memory: [[feedback-audit-unshipped-commits-before-deploy]]. Read it.
- **Robinhood MFA loop fixed at 01:58 UTC** (push approval to phone via `scripts/rh_mfa_refresh_prod.sh`). Fresh session pickle on prod. Without this, IC's broker would have failed connect → `broker_fallback_to_paper` $0 equity → qty=0 sizing → silent no-emit.
- **Service active on PID 939464 since 03:09:36 UTC, zero tracebacks.**

## Read first

1. **`BACKLOG.md` top entry (EOS 2026-05-21 ~03:30 UTC)** — canonical wrap.
2. **`runbooks/deploy_log.md`** — 03:09 UTC entry (full IC v1 ship) + 03:22 UTC entry (home tile fix) + rollback recipes.
3. **`runbooks/paper_run/ic_v1.md`** — operator playbook. Start date is filled in (2026-05-21). 30-day checkpoint = 2026-06-20; 90-day = 2026-08-19.
4. Memory (auto-loaded): `trading_corp_iron_condor_v1.md` + `feedback_audit_unshipped_commits_before_deploy.md`.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — Confirm the first IC scan window outcome
═══════════════════════════════════════════════════════════════════════════

The signal scanner fires once in the 09:45–09:50 ET window on US market days. By the time you read this:

- **If you're in this session after 13:50 UTC on a US market day** → the scan window has closed. Look for one of:
  - `IC scanner firing daily scan at 09:45 ET` in the journal (it fired).
  - Followed by combo_proposed audits if candidates qualified, OR no audits if all 5 symbols filtered out (IVR < 30, VIX > 30, macro halt within 5 days, ex-div within window, term-structure backwardation).
  - Empty-scan output is a valid outcome per the strategy docstring — don't treat it as a fault. Daily counter `scan_passes_with_no_candidates` records the filter reasons.
- **If you're in this session before 13:50 UTC on a US market day** → wait for the window, or check the historic `last_scan_telemetry_day` in `agent_state` to see if any prior day scanned.
- **If today is a weekend or 2026 NYSE holiday** → scanner skips. Confirm via `_ic_orchestration.is_us_market_day()` reasoning.

Diagnose with:

```bash
# Service status + recent logs
az vm run-command invoke -n tc-prod-vm -g rg-shared-prod --command-id RunShellScript \
  --scripts 'sudo systemctl is-active trading-corp; sudo journalctl -u trading-corp --since "today 09:30 ET" --no-pager | grep -ivE "yfinance|BTCUSDC" | grep -iE "IC scanner|IC manager|combo_proposed|combo_rejected|firing daily scan" | head -30'

# Pending combos in the registry (HITL approval queue)
curl -s https://trading.jacksumner.com/approvals | grep -A2 'combo'

# /telemetry/iron_condor live view (sections 1+3+5 are htmx 30s refresh)
curl -s https://trading.jacksumner.com/telemetry/iron_condor | head -100
```

If candidates proposed and queued for approval: Board reviews + clicks approve/reject in the dashboard. Paper mode means `data_exec.place_combo` synthesizes fills via the slippage simulator; no real money moves.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 2 — Operator daily routine kickoff
═══════════════════════════════════════════════════════════════════════════

Per `runbooks/paper_run/ic_v1.md`, the daily routine has two surfaces:

1. **`/telemetry/iron_condor`** during market hours — live open ICs, session P&L, circuit-breaker, recent combo lifecycle.
2. **`python -m trading_corp.scripts.ic_daily_digest`** end-of-day cron-able digest — combo activity, closed P&L, scan filter counters, slippage, recent errors.

Watch for:
- Combos proposed today
- Combos approved/rejected by Board
- Fills (paper synthetic — check `combo_unfilled` events indicating GFD expired)
- Slippage vs entry credit (`paper_combo_actual_vs_limit_slippage` audit field)
- Any circuit-breaker firings (`circuit_breaker_triggered` / `circuit_breaker_auto_repause`)
- Any `startup_catchup` events

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 3 — Commit the operational scripts (cleanup)
═══════════════════════════════════════════════════════════════════════════

5 untracked operational scripts in `scripts/`. The wrap session at 03:30 UTC may or may not have committed them depending on time available:

- `scripts/rh_mfa_refresh_prod.sh` — reusable RH MFA-loop fix (push approval flow).
- `scripts/deploy_ic_v1.sh` — IC patch deploy script (one-off, but reference).
- `scripts/drive_ic_v1_deploy.sh` + `.ps1` — chunked-transport driver (reusable for large-payload deploys).
- `scripts/ic_v1_deploy_finalize.sh` — assemble+extract+restart+verify.

If they're still untracked when you start, consider one commit bundling them under a message like `scripts: IC v1 deploy artifacts + reusable RH MFA refresh + chunked deploy driver`. Two are arguably IC-specific (the deploy scripts) but the chunked-transport pattern is reusable.

═══════════════════════════════════════════════════════════════════════════
## Things to NOT do without explicit approval

(Existing don'ts + IC-specific)

- **Don't flip `auto_execute: false → true` on `robinhood_joint_iron_condor`** under any circumstances pre-90-day paper-run readiness AND Board sign-off. `auto_execute_caps` block is dormant by design.
- **Don't tune any IC parameter before the 30-day tuning checkpoint** unless an outright bug surfaces. The runbook's Six Board-Authored Overrides at `runbooks/paper_run/ic_v1.md` § 30-day checkpoint govern what is and isn't appropriate to tune at that gate.
- **Don't deploy IC parameter changes mid-day if a scan has fired and combos are pending.** The strategy hot-reloads `strategies.yaml` on every `manage()` tick — mid-day changes risk inconsistent state between proposed-but-unapproved combos and the new params. Park yaml changes for after-hours.
- **Don't bundle IC v1 work with parallel-session work in any commit.** Same rule as last session — Kalshi + BitUnix + Polymarket sessions own their own files.
- **Don't deploy via `patch -p1` on routes.py** without checking CRLF normalization first (this session's deploy didn't need it because prod = LF, but it's a known sharp edge).
- **Don't attempt surgical extraction of parallel-session content from shared files** if drift exists. The auto-classifier still enforces the no-touch rule.
- **Don't push to `origin/main`** without confirming what the user wants pushed. Local is 1 ahead at session end (commit `65c8cdd`).

═══════════════════════════════════════════════════════════════════════════
## Environment notes

- **VM:** `tc-prod-vm` in `rg-shared-prod`. Public IP `20.51.145.253`. SSH may be blocked depending on your home IP; pivot to `az vm run-command invoke` per `feedback_az_run_command_when_ssh_blocked`.
- **Local Python:** `C:\Users\AA Incorporado\AppData\Local\Python\bin\python.exe` (bare `python` is the MS Store stub). Wrap with `.\scripts\run_capped.ps1` for any pytest discovery per CLAUDE.md § STOP AND READ #6.
- **Templates hot-reload in prod (Jinja).** `.py` changes do NOT hot-reload — need `sudo systemctl restart trading-corp`.
- **Robinhood session pickle:** `/home/azureuser/.tokens/robinhood.pickle`, freshened 2026-05-21 01:58 UTC. Sessions last ~14 days; next refresh due ~2026-06-04. If MFA loop returns, run `scripts/rh_mfa_refresh_prod.sh` via `az vm run-command`.
- **Prod backup tags from this session:**
  - `.pre-ic-v1-full-20260521-030935` — 12 overwritten files from the IC v1 ship.
  - `.pre-ic-tile-20260521-032240` — home.html before the tile fix.
  - `.pre-ic-v1-20260521-020956` — 4 files from the failed first-attempt patch (rollback artifact; safe to leave).

═══════════════════════════════════════════════════════════════════════════
## Service health at session start

```
Prod (tc-prod-vm):    trading-corp.service active, PID 939464
Uptime:               since 2026-05-21 03:09:36 UTC
IC tasks online:      yes (signal scanner + position manager)
RH bound:             3 accounts (individual / ira_traditional / joint_tenancy_with_ros)
auto_execute on IC:   false (load-bearing)
Pending IC combos:    0 (registry is in-process, lost on restart by design)
```

If service is no longer `active` or the IC tasks aren't visible in the journal, that's the first thing to investigate. Likely culprits: (a) RH MFA loop returned (rerun rh_mfa_refresh), (b) some other strategy crashed the process (check `journalctl` for tracebacks).

Honest assessment first: read this prompt + the EOS snapshot + the deploy_log entries before proposing any "let me just X" plan. The 03:09 UTC deploy was the result of a multi-hour audit-then-ship; don't undo that careful work without reason.
