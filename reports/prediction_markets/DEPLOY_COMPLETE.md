# Prediction Markets P1 — DEPLOY COMPLETE (2026-08-22)

All 8 steps of `DEPLOY_SEQUENCE.md` executed. This is the capstone record.

## LIVE-MONEY STATUS
P1 trades **nothing** — read-only ingestion + offline scoreboard. Engine untouched throughout:
MainPID **850993** (since 2026-08-22 02:17 UTC) verified before/after every box op. No restart, no
arm-state change, no legacy DB write attributable to this process. Legacy divisions (poly_kalshi_mlb
LIVE+ARMED+geo-blocked, MACE HALTED-weekend, PCT paper) all untouched.

## Final refs (authoritative, `git ls-remote origin`)
| ref | tip | contents |
|-----|-----|----------|
| `main` | **2c8aa23** | **UNCHANGED** (never touched — confirmed pre/post) |
| `prod-live` | **86fb433** | deployed artifacts only (package + pm_cli + config), additive on 8d77a26 |
| `prediction-markets` (durable) | **a84d164** | full P1 integrated (--no-ff merge of p1) + docs + tests + runners |
| `prediction-markets-p1` (phase) | **f88ffa5** | complete phase branch |

Branch-off base: prod-live **8d77a26** (Jack-confirmed tip).

## What ran on the box
- **Deploy:** package extracted `-C /home/azureuser/trading_corp` (nested `trading_corp/trading_corp/`,
  config at top-level `config/`). Chain-of-custody sha256 box == worktree for all 8 files.
- **Step 3 gate (Kickstand7 single-wallet):** caught the PK data-loss bug (1803 pulled → 1314 stored;
  489 two-sided binary markets collapsed). Fixed by migration 002 (PK += outcome_index) + integrity
  guard; re-validated pulled==stored==1803, both legs persist.
- **Step 4 (12-wallet backfill, 429-safe):** 12/12 COMPLETE, 28,302 closed rows, 0 FAILED/PARTIAL.
  BetMechanic mega-whale needed `--cap 50000` (17,056 rows) to reach COMPLETE.
- **Step 5 (report + acceptance):** scoreboard renders both routines (52 rows, JSON OK); coverage
  hard-bar PASS (0 in-scope-unknown after repair; 23.7% out-of-scope reported); 9 contaminated pairs
  ($-weighted dq); clip NEGLIGIBLE (max +90.2% « +200% ceiling; 0 pins). **Net-verify: SDTrading MLB
  DB == INDEP to the cent** (net 4202330.6183, cost 4659502.3177, n_resolved 469) after re-sync;
  the Step-5 1-row delta was live-whale timing. Net-loser shows negative ROI (0x71edffd0d70a −5.3%).
- **Step 6 (cron):** `20 3 * * *` (03:20 UTC) refresh `--cap 50000` installed into azureuser crontab
  (idempotent, append-only; backup `pm_cron_bak_20260823_023937.txt`). Slot re-verified clear
  immediately before (no cron/timer at 03:20). First fire 2026-08-23 03:20 UTC.

## §12 acceptance — items 1–11 PASS (see STEP5_REPORT.md for the item-by-item table)
Item 11 (branch model) completed by Steps 7–8 above.

## Rollback
- Code (prod-live / durable): the package is additive + new files; `pk_pm_rollback.ps1` removes the box
  deploy; git refs revert by reset to 8d77a26 (prod-live) / 53a86d0 (durable) if ever needed.
- Cron: restore `pm_cron_bak_20260823_023937.txt` via `crontab -u azureuser -`.
