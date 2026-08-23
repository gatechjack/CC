# Prediction Markets P1 — STEP 5 REPORT (scoreboard + acceptance evidence)

**Date:** 2026-08-22
**Branch:** `prediction-markets-p1` @ `cc8ea7f` (deployed artifacts) — durable `prediction-markets` @ `53a86d0` (docs)
**DB:** `data/prediction_markets.db` on tc-prod-vm (separate from legacy `data/trading_corp.db`)

## ★ LIVE-MONEY STATUS (leads every report)
- **This platform trades NOTHING.** P1 is read-only data ingestion + an offline scoreboard. No orders, no
  execution path, no keys wired. Nothing here places money.
- **Engine untouched throughout Step 5:** MainPID **850993** (xvfb wrapper) verified before AND after every
  box operation (backfill, scoreboard, net-verify, re-sync). No restart, no arm-state change.
- **Legacy divisions unaffected:** `poly_kalshi_mlb` (LIVE+ARMED, geo-blocked), MACE (HALTED weekend safety,
  `auto_execute` true), PCT paper farm — all untouched. No legacy DB write attributable to this process
  (scratch-pytest proved legacy `data/trading_corp.db` md5 `e659a409…` byte-identical before/after).

---

## §12 ACCEPTANCE CHECKLIST — item-by-item

| # | §12 item | Verdict | Evidence |
|---|----------|---------|----------|
| 1 | G0 passes (negative realized_pnl for a known loser) | **PASS** | `G0_RESULT.md` — G0 PASSED at build; net-losers present in scoreboard (0x71edffd0d70a unknown −$161,357). |
| 2 | `prediction_markets.db` created, separate from legacy; WAL; `init_db()` idempotent | **PASS** | Separate DB path confirmed; scratch-pytest proves legacy DB byte-untouched; re-backfill of SDTrading produced identical row count (512) = idempotent. |
| 3 | Seed-roster backfill, per-wallet isolation, ≥3,000 rows, re-run identical | **PASS** | **28,302** closed rows total (≫3,000); 12/12 wallets isolated (429 backoff); re-backfill idempotent (SDTrading 512→512). |
| 4 | Category coverage: ≥85% of live-cat rows categorize; **0 in-scope rows unknown after repair** | **PASS** | in-scope rows mis-filed as unknown after `repair-categories` = **0** (the hard bar). Out-of-scope-unknown = 6,695 (23.7%) REPORTED as a metric, not a failure (politics/crypto/macro correctly unknown). |
| 5 | `pm_category_stats` for every (wallet,cat); **one binary whale net matches independent API sum**; net-loser shows negative ROI | **PASS** | SDTrading (MLB, binary) net-verify from-scratch: **DB == INDEP to the cent** (net_realized 4202330.6183; cost_basis 4659502.3177; n_resolved 469). Net-loser negative ROI confirmed (0x71edffd0d70a −5.3%). |
| 6 | Both routines populate snapshot; `report` renders ranked table + chalk flags; `--format json` parses | **PASS** | `net_roi` + `recency_weighted` both render; chalk/contested/anomaly/contaminated flags present; **JSON OK rows=52**. |
| 7 | Engine untouched; no legacy file modified; no restart; legacy DB byte-untouched | **PASS** | MainPID 850993 unchanged; legacy md5 unchanged; deploy additive (no engine files touched). |
| 8 | §3A quarantine invariant exercised by `test_integrity.py` (clause-a AND clause-b); excluded from stats + both routines; `n_excluded`/`excluded_pnl`/data-quality verified | **PASS** (with ruling nuance) | `test_integrity.py` covers clause-(b) exclusion + event-group propagation + no-cost-basis quarantine + clause-(a) **DEMOTED to flag** (Jack's ruling §13 dec 10). Contaminated pairs surface `n_excluded` + **$-weighted** data_quality (9 pairs). |
| 9 | Full test suite green locally; live smoke run once on box | **PASS** | **61 passed / 1 skipped** locally; box scratch-pytest `pytest_exit=0` (isolated, prod DB untouched); live smoke = backfill + scoreboard + net-verify all ran on box. |
| 10 | Branch-creation gate passed (Jack-confirmed prod-live tip) | **PASS** | Branched off Jack-confirmed prod-live **8d77a26** (not stale 7150404). |
| 11 | Branch model established (phase merged; main NOT touched; prod-live advanced; runners archived) | **IN PROGRESS** | Completed by **Steps 7–8** (prod-live advance + p1→durable merge). Tracked below. |

**Verdict: §12 items 1–10 PASS. Item 11 completes in Steps 7–8.**

### Ruling nuance on item 8 (stated, not hidden)
§12 as originally written said clause-(a) rows are "proven excluded." Jack's ruling (§13 dec 10, `QUARANTINE_RECONCILE_2026-08-22.md`) **DEMOTED clause (a)** from an excluder to a non-excluding `pnl_anomaly` FLAG, because clause (a) false-positived on real single-game MLB losses (`total_bought` understates cost on scale-ins) → excluding them biased the scoreboard UP. So the test now proves clause-(a) rows are **flagged and retained (scoreable)**, and only clause-(b) [zero-cost/nonzero-realized] + event-group propagation + no-cost-basis exclude. This is the "BIAS DOWN, NEVER UP" principle (§13 dec 10) applied.

---

## NET-VERIFY (SDTrading MLB — §12 binary whale) — raw

```
raw=512 mlb=469 scoreable=469
  n_resolved   DB=469 INDEP=469
  net_realized DB=4202330.6183 INDEP=4202330.6183 delta=-0.0000
  cost_basis   DB=4659502.3177 INDEP=4659502.3177 delta=-0.0000
  VERDICT net_match=True cost_match=True nres_match=True
```
Step-5 first pass showed a −1 row / −$10,080.74 delta = live-whale timing (one MLB position resolved between
Step-4 backfill and Step-5 verify). Re-backfill + re-verify against the same snapshot → **exact match to the
cent.** Full detail: `NET_VERIFY_TARGET.md`.

---

## SCOREBOARD (routine `net_roi`, min-resolved 10) — top rows, raw
```
wallet         cat          n   win%    roiC%    roiN%     net_pnl    score  flags
----------------------------------------------------------------------------------
0x16bb9951a36f mlb        468     94    +90.2    +45.8    +4192250    1.745  CONTESTED ANOM:4
0x2dc13c6bda81 mlb        201     77    +52.6    +30.1    +1792120    1.081  CONTESTED ANOM:17
0xc3e550fae1c9 ufc        123     95    +15.3    +13.0      +24733    1.035  CHALK
0x99b1b05948d6 ufc         85     84    +38.7    +24.9      +11829    1.030  CONTESTED ANOM:3
0xc3e550fae1c9 unknown   1419     94     +5.9     +5.2      +90281    0.978  CHALK ANOM:1
```
- `roiC` = COST-based ROI (net / Σcost_basis) = **the RANKED metric** (§13 dec 11).
- `roiN` = notional ROI (net / total_bought), retained for legacy/scout comparison, **NOT ranked**.
- Full 52-row scoreboard (both routines) captured in the Step-5 evidence output.
- **Note:** the SDTrading MLB row shows n=468 at the Step-5 snapshot; after the net-verify re-sync it is
  n=469 (one newly-resolved live position). Both are correct snapshots of a live whale — this is the same
  1-row timing delta the net-verify explained, surfaced here for consistency (no unexplained contradiction).

---

## CATEGORY COVERAGE (amended bar §13A(e))
```
total rows=28302 | in 4 LIVE cats (MLB/UFC/NBA/Fed... +ranked sports)=14927 | unknown(out-of-scope)=6695 (23.7%)
in-scope rows mis-filed as unknown after repair (MUST be 0): 0     <-- HARD BAR: PASS
category counts: nba 7166, unknown 6695, mlb 5326, nhl 2444, ufc 2205, nfl 1533, cs2 1249,
  soccer 249, fed 230, atp 222, epl 203, fifwc 191, ucl 157, tennis 154, wnba 136, wta 82, cbb 57, golf 3
```

## CONTAMINATED (wallet,category) pairs — $-weighted data_quality (9 pairs)
```
   0x2fb0f88ef5 fed     nres=6   nexc=15  count%=71.4  $%=100.0
   0x6dd6314d16 nfl     nres=0   nexc=2   count%=100.0 $%=100.0
   0x2fb0f88ef5 soccer  nres=57  nexc=11  count%=16.2  $%=96.2
   0x2fb0f88ef5 unknown nres=463 nexc=471 count%=50.4  $%=96.2
   0x2fb0f88ef5 ucl     nres=47  nexc=6   count%=11.3  $%=93.8
   0x2fb0f88ef5 epl     nres=46  nexc=29  count%=38.7  $%=39.5
   0xd1acd3925d nba     nres=171 nexc=6   count%=3.4   $%=22.3
   0xd1acd3925d ufc     nres=95  nexc=6   count%=5.9   $%=13.2
   0x2fb0f88ef5 nba     nres=165 nexc=28  count%=14.5  $%=0.0
```
Data-quality is dual-flagged (count% OR $% >10%) — the $-weighted view is the load-bearing one
(§13 dec 10): a pair with few excluded ROWS but most excluded DOLLARS is still contaminated.

## CLIP SATURATION (measurement only — NOT retuned, per Item 2)
```
scored pairs: 52
cost_roi min=-10.6%  median=5.9%  max=90.2%
pinned CEILING(>=+200%): 0 | pinned FLOOR(<=-50%): 0  ->  NEGLIGIBLE
```
On corrected (cost-based) data, no scored pair pins the `_edge_factor` clip. Retuning is DEFERRED
(§13A(g)/Item-2) — measured, left alone.

---

## STOP-TRIGGERS: none fired
No engine PID/arm change, no legacy DB write, no unexplained metric contradiction (the 1-row net-verify
delta was explained + closed), nothing needing sudo/restart/existing-file edits.
