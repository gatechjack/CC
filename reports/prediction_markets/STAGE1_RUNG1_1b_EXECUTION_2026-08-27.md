# Stage 1 — RUNG 1 (migration 009) + RUNG 1b (grace 72h) — EXECUTION RECORD (2026-08-27)

**Authorization (Jack, 2026-08-27):** "STAGE 1 RUNG 1 + RUNG 1b — AUTHORIZED. Live migration 009, then the 72h
config write. This authorizes rungs 1 and 1b ONLY. Rung 2 (deploy) and rung 3 (poller/adjudicator cadence)
remain UNAUTHORIZED. No deploy, no pm_web restart, no poller run, no adjudicator run." Amendment: pm_closed_position
is report-only, not an abort gate. **Both rungs executed clean; no deploy, no restart, no poller/adjudicator run.**

Branch tip executed from: **0dd8825** (`prediction-markets-stage0-2026-08-26`, worktree clean).
Window: **16:24–16:28 UTC**, clear of the 03:20 UTC cron refresh. All box ops ran **as azureuser** (no root; a data-plane
DB migration + one config UPDATE need no root — no restart was performed).

---

## RUNG 1 — migration 009 (PURE DDL), live

### Pre-conditions (read-only) — all matched
| Check | Expected | Live |
|---|---|---|
| UTC / cron window | clear of 03:20 | 16:24:22Z |
| schema_version | 8 | 8 |
| pm_paper_category_stats | absent | absent (0) |
| pm_watchlist total/active/inactive | 114 / 92 / 22 | 114 / 92 / 22 |
| pm_paper_trade | 102 | 102 |
| pm_closed_position (report-only) | ~29839 | 29839 (unchanged; nothing wrote since yesterday's refresh) |
| pm_paper_config count / grace | 3 / 172800 | 3 / 172800 |
| /farm | 200, 182835 B | 200, 182835 B |
| engine / pm_web MainPID | 676 / 652 | 676 / 652 |

DB owner `azureuser:azureuser`, 25137152 B. Box-identity: pm_web PID 652 unchanged since the post-reboot
box==c77f618 verify → runtime code is still c77f618 (same loaded process; /farm 182835 confirms its behavior).

### Byte-verified ephemeral scratch (NO db.py deployed)
- Local reference (git blob of `0dd8825:trading_corp/prediction_markets/db.py`): **392e182742907519e0de650d5432956ddeddc3d7**
- Local SHA256: **106e2b0333c7fbf99cb0e310c9107d30071f1a6401ab2ff7c10ccde048883815**, 38440 bytes
- Package tarball `git archive 0dd8825 trading_corp` scp'd to box → extracted to `~/pm_rung1_scratch`
- Box re-hash of scratch db.py: **SHA256 match + git-blob match + 38440 bytes** → `BYTE_VERIFY=OK`
- Applied by loading the scratch db.py **standalone via importlib** (db.py has zero relative imports — stdlib only),
  then `init_db("<LIVE>")`. This runs only db.py's version-gated migration loop; no package `__init__`, no engine
  import, no runtime db.py replaced. Scratch dir + tar **removed** after post-verify.

### Gate-1 online backup (integrity-gated)
- `~/pm_stage1_rung1_dbbackup_20260827T162523Z.db` — 25137152 B
- SHA256 **8e4c510ce93cba1e6b297759c1e1296359ebe2acda0cb53ef07022a43f4d0f03**
- `PRAGMA integrity_check = ok`; backup verifies schema 8, closed 29839, watchlist 114. (Restore point retained.)

### Apply + post-verify — all matched
- `SCRATCH_MIGRATIONS_MAX=9`; `SCHEMA_BEFORE=8` → `init_db` → `SCHEMA_AFTER=9`; `schema_version` rows `[1..9]`.
- pm_paper_category_stats **present, 15 columns** exactly:
  `wallet,category,n_closed,wins,losses,win_rate,net_paper_pnl,cost_basis,roi,avg_entry_price,n_open,n_stale,n_void,last_resolved_ts,updated_ts`;
  rowcount **0**; index `ix_pm_pcs_category_roi` **present**.
- **PURE-DDL PROVEN LIVE:** pm_paper_config count still **3**, grace still **172800** (009 wrote no config).
- Counts unchanged: watchlist 114/92/22, paper_trade 102, closed 29839.
- /farm **200, 182835 B (byte-identical)**; PIDs **676 / 652** unchanged.

**Rung 1 = clean. Live PM DB is now SCHEMA 9. Runtime code on the box remains c77f618 (data-plane only).**

---

## RUNG 1b — grace window 172800 → 259200 (72h), live

Single authorized `UPDATE`, guarded (pre-read → assert → write → commit → fresh-read post-verify):
- PRE: exactly **1** row `key='grace_window_sec'`, value **172800** (prior updated_ts NULL).
- `UPDATE pm_paper_config SET value='259200', updated_ts=1787848057 WHERE key='grace_window_sec'` → **ROWS_UPDATED=1**.
- POST: value **259200**, updated_ts 1787848057; pm_paper_config count still **3**
  (`grace_window_sec=259200, poll_interval_sec=300, size_basis=100`).
- /farm **200, 182835 B** unchanged; PIDs **676 / 652** unchanged.

**Reversibility:** `UPDATE pm_paper_config SET value='172800' WHERE key='grace_window_sec'`; DB-level restore =
the Gate-1 backup above. `paper.CONFIG_DEFAULTS['grace_window_sec']=259200.0` is the matching code default (arrives
on Rung 2 deploy).

---

## STATE AFTER RUNGS 1 + 1b
- Live PM DB: **schema 9**; pm_paper_category_stats present (empty, 15 cols, indexed); grace **259200** (72h).
- Unchanged: pm_watchlist 114/92/22, pm_paper_trade 102, pm_closed_position 29839, /farm 182835 B, engine PID 676,
  pm_web PID 652. **Runtime code still c77f618 — no deploy, no restart.**
- Backup retained: `~/pm_stage1_rung1_dbbackup_20260827T162523Z.db` (sha 8e4c510c…, schema 8).

## STILL UNAUTHORIZED (per ruling)
- **Rung 2** (deploy the 5 gated code files + pm_web restart). The new table is present but no runtime code queries
  it yet — that is Rung 2.
- **Rung 3** (poller/adjudicator cadence). Cadence is RULED — path (b) start polling, `*/30`, order
  poll → adjudicate → rollup — but "this is the ruling, not the go." Poller/adjudicator NOT run.
- Tier-2 poller categorization gap = **separate ticket** (not Stage 1).

## Runners (cc\, pure ASCII, streamed via tr -d CR/BOM | bash)
`pm_rung1_precheck.sh` · `pm_rung1_extract_verify.sh` · `pm_rung1_backup.sh` · `pm_rung1_apply.sh` ·
`pm_rung1_postverify.sh` · `pm_rung1b_grace.sh` (+ package tarball `pm_rung1_pkg_0dd8825.tar`).
