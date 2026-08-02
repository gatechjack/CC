# Bitunix ops backlog — burn-down report (2026-08-02)

Read-only housekeeping pass. Non-deploy chores executed; deploy-gated items are
written proposals only (see `PROPOSALS.md`). No prod trading logic touched. Agent
prod SSH was **classifier-blocked** this session → items needing live prod data
carry an operator-runnable query instead.

---

## B1 — Stale-worktree cleanup (EXECUTED)

`git worktree list` on `C:\Users\AA Incorporado\CC`: **114 → 84** worktrees.
Method (per `worktree-backlog-cleanup-task.md`): per-worktree `status --porcelain`
empty AND `merge-base --is-ancestor <tip> origin/main`, then `worktree remove`
(no `--force`) + `branch -D`. Re-verified clean+merged at delete time. Never bulk
`--force`, never `stash`/`clean`.

| class | count | action |
|---|---|---|
| **REMOVE-safe** (clean + merged to origin/main) | **30** | **removed (wt+branch)** |
| KEEP-unmerged (tip not ancestor of origin/main) | 57 | kept — squash-merged / prod-live-only / audit branches; not auto-prunable |
| KEEP-dirty (uncommitted/untracked work) | 22 | kept — **listed below for review** |
| PROTECT (main checkout, accrual home, 2 agent-locked, 1 detached) | 5 | untouched |

**30 removed:** 10× June `bitunix-*` fixes, 3× `n2` exit-path, `pmcc-lifecycle-forensic`,
7× `polymarket-e1-*`, 4× `polymarket-option-c-*`, and 5 `cc-*-wt` (deblock, kalshi-k5,
killpaper, reconcile, staleness). All June/early-July, all provably in `origin/main`.

**22 KEEP-dirty (operator review — have uncommitted work, NOT deleted):**
`bitunix-b2-maker-execution-2026-06-15` (holds b2_recovery artifacts — keep),
`bitunix-scalp-tp-recalibration`, `bitunix-sfp-2026-06-25`, `bitunix-untaken-trades-deep-dive`,
`htf-proximity-audit`, `robinhood-pead`, `stage1-redeploy`, plus recent `cc-*-wt`:
`cc-2026-07-29/07-31/07-31d/08-01b/08-02b/08-02c` (today's — likely active sessions),
`cc-bull-bottleneck`, `cc-claude-2026-07-26`, `cc-htf-sweep`, `cc-kalshi-k5-b`,
`cc-native-etl` (holds the native-ETL build), `cc-p3`, `cc-precursor`, `cc-sfp-deploy`,
`cc-wallet-pol-swap`. Recommend: eyeball each, commit-or-discard, then a second prune pass.

> The **57 KEEP-unmerged** are mostly squash-merged or prod-live-only feature branches
> (the conservative `is-ancestor` gate marks them unmerged = the safe error). A future
> deeper pass could patch-id/cherry-check them, but that's higher-risk — not done here.

---

## B2 — btc_scalping.db backup + restore path (EXECUTED)

There is **no cloud-backup pattern in the repo** and **0 storage accounts in the
Azure subscription** (only `rg-shared-prod` + NetworkWatcherRG) → a managed Azure
Blob target would be a deploy-gated storage-account provision. The machine's
existing cloud channel is **OneDrive** (`C:\Users\AA Incorporado\OneDrive`,
OneDrive.exe running).

`scripts/backup_corpus_db.py` follows the repo idiom (online `sqlite .backup` +
`PRAGMA integrity_check`), copies the verified backup off-machine, and **proves the
restore path** by re-opening the dest copy read-only and matching integrity +
table/row shape to the source.

- source `data/btc_scalping.db` 75.1 MB, 9 tables, `integrity_check=ok`.
- backup → `%LOCALAPPDATA%\trading_corp\backups\` (off-repo staging) **and**
  `OneDrive\Backups\trading_corp\` — both `integrity_check=ok`, shape-match, identical
  md5 `8c16d6d43c375316c3ea56a1ae30ceb8`. Retention keep=5, append-only `backup_log.tsv`.
- **RESTORE:** a `.bak` is a complete standalone SQLite file; copy it over
  `data/btc_scalping.db` (see the script header). Verified restorable from the dest copy.
- ⚠ Confirm the OneDrive file shows the green "synced" check for true off-machine
  durability (folder was empty at write time). Azure Blob = deploy-gated alternative.

---

## B3 — Corpus refresh (DUE / overdue — operator-gated, NOT executed)

The corpus (`data/btc_scalping.db`) is refreshed by **ingesting operator-provided
TradingView exports** of `BYBIT_BTCUSDT.P` (Cypher+Otter indicator lineup) via
`scripts/ingest_tv_export.py`, plus one Bitunix-native 3m via `ingest_bitunix_bars.py`.
A TV chart export is a manual GUI step — **I cannot generate it**, so the 6-TF refresh
is inherently operator-gated.

**Window status: DUE / overdue.** Last ingest **2026-06-19** (~6.5 weeks ago);
coverage ends 06-19 across every TF. Any monthly cadence triggered ~07-19.

Before-counts per TF (baseline for the refresh):

| TF (table) | rows | coverage end |
|---|---|---|
| bars_1m | 50,389 | 2026-06-19 00:26 |
| bars_3m | 38,899 | 2026-06-19 00:54 |
| bars_3m_bitunix | 16,387 | 2026-06-18 23:24 |
| bars_15m | 22,086 | 2026-06-19 01:15 |
| bars_30m | 25,635 | 2026-06-19 01:00 |
| bars_1h | 16,398 | 2026-06-19 01:00 |

**To refresh:** hand fresh TV exports (1m/3m/15m/30m/1h BYBIT_BTCUSDT.P, full signal
lineup) → `ingest_tv_export.py` upserts on `ts` (ON CONFLICT), sha256 file-dedup, so
re-ingest only enriches/appends (existing rows safe; back up first — B2). A partial
bitunix-3m-only refresh from a prod extract is possible but would mix data vintages
(1 TF at 08-02, 5 TFs at 06-19) — **not recommended without your call.**

---

## B4 — ETH freshness-filter WATCH (analysis only, no change)

**Symptom (07-26 weekly review):** ETH SFP shut out — 0 trades / 13d despite 8+
rd-gate passes — by `sfp_skip_not_fresh_inst`, i.e. the L3 fresh-institutional gate,
not the trend gate.

**Mechanism (verified, `bitunix_sfp_observer.py:868-878` + `bitunix_inst_levels.py`):**
after the rd/ps trend gate, the swept level is tagged by `InstLevels(...).tag(level, entry_ts, side)`:

    if not (tg["at_institutional"] and tg["freshness"] == "fresh"):
        audit "sfp_skip_not_fresh_inst" {at_institutional, freshness, tier, kinds}; return

- `at_institutional` = swept level within `TOL_ATR·ATR15` (**TOL_ATR = 0.15**) of a
  PRIOR-period institutional level (PDH/PDL, PWH/PWL, PMH/PML, session hi/lo).
- `freshness` = `fresh` unless price already crossed that level since the active
  period start (then `broken`).

**The skip is self-diagnosing** — its payload records exactly WHY:
- `at_institutional=False` → sweep wasn't near an institutional level. **Admittable by
  widening TOL_ATR (0.15→X).** The tunable.
- `at_institutional=True, freshness="broken"` → level was at-institutional but already
  violated. **Structural — no threshold admits these** short of dropping the fresh rule.

**What "threshold movement would admit" — operator-runnable read-only query** (SSH
was classifier-blocked; run on prod under the service env):

```sql
-- ETH SFP funnel since the 07-13 gross->net seam
.mode column
.headers on
SELECT kind, COUNT(*) n FROM audit_event
 WHERE actor='bitunix_sfp' AND ts>='2026-07-13'
   AND json_extract(payload_json,'$.symbol') LIKE '%ETH%'
   AND kind IN ('sfp_rd_gate_pass','sfp_ps_gate_pass','sfp_skip_not_fresh_inst',
                'sfp_skip_no_inst_source','sfp_skip_inst_error',
                'would_have_placed','live_order_placed')
 GROUP BY kind ORDER BY n DESC;

-- WHY each fresh-inst skip fired -> the threshold-movement split
SELECT json_extract(payload_json,'$.at_institutional') AS at_inst,
       json_extract(payload_json,'$.freshness')        AS freshness,
       json_extract(payload_json,'$.tier')             AS tier,
       COUNT(*) n
  FROM audit_event
 WHERE actor='bitunix_sfp' AND kind='sfp_skip_not_fresh_inst'
   AND ts>='2026-07-13' AND json_extract(payload_json,'$.symbol') LIKE '%ETH%'
 GROUP BY at_inst, freshness, tier ORDER BY n DESC;
```

Run read-only: `sqlite3 -readonly 'file:/home/azureuser/trading_corp/data/trading_corp.db?mode=ro'`.

Row 2 gives the answer directly: `at_inst=0` count = admittable by widening TOL_ATR
(and by how much — needs recompute of sweep-to-level distance, a corpus/harness task);
`at_inst=1, freshness=broken` count = structural, only removing the fresh requirement admits them.

**R-impact estimate (grounded, no live numbers needed to bound direction):** the
fresh-inst gate is what makes ETH SFP selective. The full SFP research arc found **ETH
SFP is NOT an edge** — net −0.140R @2R (direct) / −0.371R (BOS); the best target-matched
cell is ~breakeven (+0.044R @1.0R, single-quarter, n=22) — "monitor-only, not promoted"
(`bitunix-native-etl-built`). So loosening the gate to admit the shut-out setups most
likely adds **negative-to-breakeven R** — the gate is doing its job. A precise per-setup
R needs each skipped setup's post-signal excursion simulated (harness on the — currently
stale — corpus, or forward data). **Recommendation: WATCH, do not loosen** until the
query shows a large `at_inst=1/fresh`-adjacent population AND a harness run shows positive
WF-stable R on the admitted subset. Evidence only; Board decides.
