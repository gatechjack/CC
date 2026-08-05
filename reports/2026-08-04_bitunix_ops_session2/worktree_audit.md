# TASK 1 — B1 second-pass dirty-worktree audit (2026-08-05)

Read-only forensic pass over the **22 KEEP-dirty worktrees** carried out of the 2026-08-02
B1 prune (`reports/2026-08-02_bitunix_ops_backlog/REPORT.md`). **Nothing changed, nothing
deleted, no `stash`/`clean`/`--force`.** Method: per-worktree `git status --porcelain`,
`merge-base --is-ancestor HEAD origin/main`, `rev-list --count` (ahead/behind), `log -1`,
and read-only diff/file inspection to characterize the uncommitted content. Recommendations
are for **operator ruling row by row** — the agent recommends, it does not act.

Merge column: **Merged? = tip is an ancestor of `origin/main`** (exit 0). Most session
branches are squash-merged or prod-live-only, so "NO" here means "not auto-prunable by the
conservative ancestor gate", not necessarily "unshipped".

| # | Worktree | Branch | Merged? | Ahead/Behind main | Last commit | Uncommitted (files → characterization) | Rec |
|---|---|---|---|---|---|---|---|
| 1 | `bitunix-b2-maker-execution-2026-06-15` | `bitunix-b2-maker-execution-2026-06-15` | YES | 0 / 265 | 2026-06-15 `ef6fa5f` | 1 tracked mod `scripts/pine/coinbase_btc_hodl.pine` (+17, real) + 58 untracked (~36 `.sh`, 10 `.ps1`, 5 `.txt`, 4 `.py`, `.scratch/`) → **real-work pine + scratch** | **COMMIT** pine to branch, then prune scratch |
| 2 | `bitunix-scalp-tp-recalibration` | `bitunix-scalp-tp-recalibration-2026-06-10` | NO | 4 / 370 | 2026-06-10 `4385b8c` | `tpdata.out` (5,154-line OHLCV dump) → **scratch** | **DISCARD** (regenerable dump; no source) |
| 3 | `bitunix-sfp-2026-06-25` | `prod-reconcile-2026-06-28` | YES | 0 / 153 | 2026-06-28 `85faacf` | `_prodsnap/` md5/html snapshots + deploy staging + 2 run logs → **scratch** | **DISCARD** (branch merged) |
| 4 | `bitunix-untaken-trades-deep-dive` | `bitunix-untaken-trades-deep-dive-2026-06-10` | NO | 2 / 386 | 2026-06-10 `fec53ec` | `qdata.out` (884-line OHLCV dump) → **scratch** | **DISCARD** (dump only) |
| 5 | `htf-proximity-audit` | `htf-proximity-audit-2026-06-01` | NO | 2 / 447 | 2026-05-31 `87550a8` | `.scratch/htf_proximity_audit/` (tsv data + probe `.py`/`.sql`) → **scratch** | **DISCARD** scratch |
| 6 | `robinhood-pead` | `robinhood-pead-2026-06-20` | NO | 39 / 215 | 2026-06-26 `774cd8b` | 3 probe scripts (`frac_learn_probe*.py`, `pickle_refresh.py`) + 6 `.log` → **mixed** | **COMMIT** 3 probes to branch; discard logs |
| 7 | `stage1-redeploy` | `stage1-redeploy-2026-05-30` | NO | 1 / 460 | 2026-05-30 `c95058b` | `.scratch/` rolled-back-deploy artifacts → **scratch** | **DISCARD** |
| 8 | `cc-2026-07-29` | `claude-2026-07-29` | NO | 64 / 0 | 2026-07-30 `4d71479` | 64 untracked auth/deploy probe `.sh`/`.ps1`/`.txt`/`.b64` → **scratch** | **DISCARD** untracked |
| 9 | `cc-2026-07-31` | `claude-2026-07-31` | NO | 65 / 0 | 2026-07-31 `06a800f` | `_sfp_fix/` deploy staging (`.new` + verify snapshots) → **scratch** (fix already deployed+committed) | **DISCARD** |
| 10 | `cc-2026-07-31d` | `claude-2026-07-31d` | NO | 76 / 0 | 2026-07-31 `fb97c96` | `_p2_deploy_root.sh`, `_p2_rollback.sh` (superseded by `pmcc_p2_rollback_20260731.sh`) → **scratch** | **DISCARD** |
| 11 | `cc-2026-08-01b` | `claude-2026-08-01b` | NO | 94 / 0 | 2026-08-01 `4f61dcf` | `kc2_pull.ps1`, `kcv2_s0_check.ps1` diagnostic probes → **scratch** | **DISCARD** |
| 12 | `cc-2026-08-02b` | `claude-2026-08-02b` | NO | 90 / 0 | 2026-08-02 `6326a36` | `_deploy_bundle/` + `pead_deploy.tar.gz` (completed PEAD deploy) → **scratch** | **DISCARD** |
| 13 | `cc-2026-08-02c` | `claude-2026-08-02c` | NO | 95 / 0 | 2026-08-02 `5f56ccc` | `_cd/` staging copies of card files already tracked under `web/` → **scratch** | **DISCARD** |
| 14 | `cc-bull-bottleneck` | `bull-bottleneck-scorer-pa-2026-06-19` | NO | 1 / 233 | 2026-06-19 `78c0a19` | `data/bull_bottleneck/cleared_buy.csv` (1,470 rows, regenerable) → **scratch** | **DISCARD** (low-stakes; conclusion in committed report) |
| 15 | `cc-claude-2026-07-26` | `claude-2026-07-26` | NO | 49 / 0 | 2026-07-27 `0bdc3e0` | `deploy_tmp/` LF-normalized staging copies → **scratch** | **DISCARD** |
| 16 | `cc-htf-sweep` | `htf-regime-timeframe-sweep-2026-06-19` | NO | 1 / 233 | 2026-06-19 `ef9a93a` | `data/htf_sweep/*.csv` (993 + 19,591 rows, regenerable) → **scratch** | **DISCARD** (hypothesis refuted in commit) |
| 17 | `cc-kalshi-k5-b` | `kalshi-k5-dashboard-2026-06-30` | YES | 0 / 125 | 2026-06-30 `7614b01` | `reports/2026-06-30_k5_workstream_b.md` — substantive session-wrap report, **never committed** → **real-work** | **COMMIT** the report to branch before any prune |
| 18 | `cc-native-etl` | `bitunix-native-etl-2026-06-18` | NO | 4 / 319 | 2026-06-18 `f081fe1` | `data/native_extracts/`: `_verify.py` (utility) + CSVs/DB/JSON (generated) → **mixed** | **COMMIT** `_verify.py`; discard data artifacts |
| 19 | `cc-p3` | `claude-2026-07-31e` | NO | 80 / 0 | 2026-07-31 `dafe60b` | `_p3_deploy_root.sh`, `_p3_rollback.sh` → **scratch** (rollback anchor is `pmcc_p3_rollback_20260801.sh`) | **KEEP-AS-IS** (rollback copy, low cost) |
| 20 | `cc-precursor` | `otter-div-precursor-2026-06-19` | NO | 1 / 233 | 2026-06-19 `7e65793` | `data/precursor/_charcols.py` (utility) + `stepA.json` (scratch) → **mixed** | **COMMIT** `_charcols.py`; discard `stepA.json` |
| 21 | `cc-sfp-deploy` | `sfp-bidirectional-deploy-2026-07-01` | NO | 16 / 104 | 2026-07-01 `b849964` | `deploy_bidirectional_sfp/` pytest output + prod snapshot → **scratch** | **DISCARD** (verification artifacts) |
| 22 | `cc-wallet-pol-swap` | `wallet-ops-pol-swap` | YES | 0 / 233 | 2026-06-18 `1c12d5c` | 5 `_phase2_*.py` monitoring utilities (untracked source) + 4 `.cmd` → **real-work** | **COMMIT** the 5 `_phase2_*.py`; `.cmd` optional |

## Verification notes

- **All 22 still exist and still hold uncommitted work** — none went clean or was removed since 08-02.
- **No cross-agent-live-session flags** among the 22: zero `.db-wal`/`.db-shm` (no open SQLite
  handles), the newest (`cc-2026-08-02b/c`) hold stale completed-deploy bundles, not mid-flight work.
- **Peer today-dated worktrees (NOT in the 22, left untouched):**
  - `cc-2026-08-04-wt` (branch `claude-2026-08-04`) — **this session's active worktree**. KEEP-AS-IS.
  - `cc-2026-08-04b-wt` (branch `claude-2026-08-04b`) — a **separate** clean today-dated worktree
    the audit found; not created by this session. Left untouched (may be another session). ⚠ Operator:
    confirm whether this is yours before any prune — treat as possibly-active.
  - `cc-2026-08-02-wt` (branch `claude-2026-08-02`) — **prune-exempt** kcv2 accrual home (228MB lab DB
    + scheduled jobs). KEEP-AS-IS always.
  - Two `.claude/worktrees/agent-*` locked worktrees — not in the 22, untouched.

## Summary for operator ruling

- **6 worktrees hold COMMIT-worthy content** (real work to save before any prune): **#1** (pine
  indicator edit), **#6** (3 PEAD probe scripts), **#17** (a full unsaved session-wrap report), **#18**
  (`_verify.py`), **#20** (`_charcols.py`), **#22** (5 wallet Phase-2 utilities). Recommend: commit the
  named files to each worktree's own branch first, then a prune pass can proceed.
- **14 worktrees are pure scratch → DISCARD candidates** (#2,3,4,5,7,8,9,10,11,12,13,14,15,16,21 —
  deploy staging, run outputs, regenerable data dumps; no forward source value). Note #14/#16 hold
  regenerable analysis CSVs whose conclusions already live in committed reports.
- **1 KEEP-AS-IS** (#19 `cc-p3` — local rollback-script copy; live anchor exists elsewhere; harmless).
- **Next-pass note:** after the operator commits the 6 real-work items, worktrees whose branches are
  ancestors of `origin/main` (#1, #3, #17, #22 — the "YES" rows) become clean+merged and safe for a
  second `worktree remove` (no `--force`) pass. The "NO"-merged rows with commits ahead (#6, #8-#13,
  #18, #19, #21) are unmerged branches and stay until squash-merge status is checked (out of scope here).
