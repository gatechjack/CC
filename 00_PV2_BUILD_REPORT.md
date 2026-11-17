# POLYMARKET DATA API v1→v2 MIGRATION — BUILT, TESTED, COMMITTED, STAGED. HALT FOR JACK'S DEPLOY.

**Date:** 2026-11-17 · **Branch:** `pm-polyv2-migration-2026-11-17` off verified `origin/prod-live` `f82d5be6`
(fetch-confirmed; box files == prod-live CR-stripped before work began). **Nothing deployed, restarted, armed,
pushed or written to any prod DB.** All box work ran in `/home/azureuser/pv2_scratch` (cleaned) against the box
venv; the live tree was only ever READ.

## The change

| | file | BASE (box==prod-live, verified) | TARGET |
|---|---|---|---|
| 1 | `trading_corp/data/polymarket_data_api_client.py` | `ad9a31922becfbe1` | `c88cfce6a16cc4a0` |
| 2 | `trading_corp/prediction_markets/loss_grounding.py` (comment only) | `ce22b4d26de6faf4` | `42d166575b2f39bd` |

Diff: client 612 lines changed (+464/−155 net across both files); tests +1,012 lines (2 new files).
Commits: **`00a205c4`** (migration) · **`75dbd048`** (tests) · this report + runners follow. *(shas are
CR-stripped `sha256sum | cut -c1-16`, the box convention.)*

Five parts, translation at the client boundary only — **every consumer sees exactly what it saw from v1**:
1. **URLs:** `/v2/positions`, `/v2/activity`, `/v2/leaderboard`; `/closed-positions` → `/v2/positions?status=CLOSED`
   (merged in v2). gamma-api resolutions are a separate host, untouched by the retirement — unchanged.
2. **Envelope:** `{"data":[…],"pagination":{…}}` unwrap; **malformed envelope RAISES**. (The v1 code's
   `isinstance(rows,list) → return []` turned any unexpected payload into a silent empty book — which the
   snapshot diff reads as the whale exiting everything. That path is dead.)
3. **Cursor-only pagination:** live-verified — `offset=` (even `offset=0`) → HTTP 400 *"pages are cursor-only"*.
   Public `offset` signatures are kept and translated internally (per-client cursor cache: sequential paging =
   one request per page; cold offsets walk forward, bounded at 200 pages, loud on overflow; offset past the end
   returns `[]` exactly as v1 did). **Terminal = absence of `next_cursor`, never row count** (a silently-capped
   `limit` cannot read as end-of-book).
4. **Field remap** to v1 names/units/meanings: `size←current_size`, `asset←token_id`,
   `initial_value←entry_cost_usdc`, `pnl←unrealized_pnl`, `total_bought←total_size` (SHARES notional, the
   §13A(g) semantic ingest depends on), `cur_price←current_price`, `timestamp←last_event_at`,
   `opposite_asset←opposite_token_id`; leaderboard `proxy_wallet←user_id`, `vol←volume`,
   `verified_badge←verified`, `sortBy→sort_by`.
5. **Failure is loud:** 410 (tagged *"endpoint retired/gone"*) / non-retryable 4xx / exhausted 5xx / exhausted
   429 / timeout / malformed envelope → **`HARD-FAIL` ERROR log, rate-limited to 1 line per endpoint per 60 s
   with a suppressed-count**, + typed raise. 429 now backs off with jitter (5/15/30 s base ± 50 %) before
   raising — no hot loop, every retry sleeps.

## §1 Timestamps — the ms/s question, settled against payloads

**v1 already served epoch seconds for every field we consume and v2 still does** — verified three ways: the v1
parser has no `/1000` anywhere and its docstrings say "unix seconds" (`:155,:289` of the BASE file); captured
live v2 payloads carry `timestamp:1794890329`, `last_event_at:1781574820` (seconds); the box activity probe
confirmed `timestamps_in_seconds_range: true` over 100 live rows. **No conversion performed.**

Complete inventory of consumers doing arithmetic/comparison on client-delivered times, each with a test that
fails at 1000× in either direction:
- **Exit window** — `live_driver.py:662` (`activity_sells_from_activity` ts) → `execution.py:875`
  (`|reduction.ts − sell.ts| ≤ _EXIT_WINDOW_SEC=300`, `live_driver.py:641`). Test:
  `test_exit_window_consumer_pairs_in_seconds` (pairs at +120 s; ms-corrupt ×1000 and ÷1000 both yield zero exits).
- **Recency gate** — `ingest.py:143` (`resolved_ts ← ClosedPositionRow.timestamp`) → `pm_category_stats` →
  `search.py:261` (`lt ≥ now − 30d·86400`). Tests: `test_recency_gate_consumer_in_seconds` +
  `test_v2_timestamps_parse_in_epoch_seconds_range` (range-pins 1.5e9–2.2e9).
- **Checked and NOT client-fed** (no test needed): `_SETTLED_LOOKBACK_SEC` (`live_driver.py:89-106,226…`)
  filters **Kalshi** catalog close_ts; paper grace/poll windows and `pm_open_position.refreshed_ts` are our
  clock; heartbeat freshness is our clock; idempotency `signal_id` uses no timestamp for entries
  (`live_driver.py:627`) and only as a tx-hash **fallback string** for exits (`execution.py:879`) — identity,
  not arithmetic.

## §2 Side and outcome — the wrong-leg risk, found and killed

The dangerous coupling was NOT in the matchers (they consume `(slug, outcome, title)` strings, which v2 serves
byte-compatibly) but in **`PositionRow.extra`**: `paper.py` reads four raw v1 keys **by name** —
`pos_outcome_index→"outcomeIndex"` (`paper.py:96`), `is_genuinely_open→"curPrice","redeemable"` (`:111,:113`),
`pos_end_date→"endDate"` (`:100`). Under raw v2 keys, `outcomeIndex` misses → **index 0 for every position (the
wrong leg for every index-1 leg)** and `curPrice` misses → 0.0 → **every position reads settled → zero entry
signals, silently**. The new `from_api` synthesizes all four v1 keys from v2 fields
(`polymarket_data_api_client.py`, PositionRow.from_api); `test_position_row_extra_contract_drives_real_paper_consumers`
pins it on the real captured payloads (index-1 "Under" row included), and every matcher-equivalence test asserts
`pos_outcome_index` en route.

**Per-category matcher equivalence** (`tests/prediction_markets/test_v2_matcher_equivalence.py`, 16 tests, all
passing): raw v2 dict → `PositionRow.from_api` → the category's parse+match resolves the **same Kalshi (ticker,
leg)** the v1-era fixtures derive from the ticker's own code + title + whale-buys-YES convention (never from
matcher output): itf (men + women + a plain-strings CONTROL proving the client boundary adds/loses nothing),
boxing, f1, mlb (moneyline + team-total Over→yes/Under→no), nfl (moneyline + total), soccer/mls (win-Yes→yes,
win-No→no), atp, ufc, cs2. **nba:** no fixture exists anywhere in tests/ (not invented); it shares the
structural matcher code path with nfl (`execution.py:597-598`) — documented in the module docstring.
**One live v2 string delta, flagged:** v2 drops apostrophes in outcome labels (`St. John's Red Storm` →
`St. Johns Red Storm`, seen in parity). Matcher name-joins are code/subsequence-based and the live matcher tests
pass on real shapes; noted as residual-watch for the first live fills' `leg_audit`.

## §3 Closed positions + activity — same data, same numbers, proven on stored rows

- `total_bought ← total_size` (SHARES) proven twice: against the API's **own** `percent_realized_pnl` on the
  captured Belgium row (`realized/(total_size·avg) = 192.67 %` exactly), and **value-for-value against stored
  v1-era rows on the box**: 3 wallets, 200 v2 closed rows each, joined on (wallet, condition_id, outcome_index):
  **333 joined → 312 value-equal (94 %)** on avg_price AND total_bought AND realized_pnl AND cur_price AND
  outcome. The 21 non-equal: every inspected one has `v2_total > stored_total` with lower avg — the whale **kept
  trading the position after our last ingest (2026-11-02)**; the stored row is a stale snapshot, not a semantic
  mismatch (2 of them additionally differ only by the apostrophe normalization above).
- **Partial sale** (the +80 %→+200 % ROI trap): the open-row identity `pnl = current_value − initial_value`
  (unrealized only, realized excluded) held on **all 14,343 live open rows with realized ≠ 0** — zero violations.
  `ingest.cp_to_record` value parity (cost_basis, shares_derived, won, resolved_ts) unit-tested on the real row.
- `timestamp ← last_event_at`: vs stored `resolved_ts` the delta is **median 14–43 s** (max ~1.7 d, a late-redeem
  tail) — irrelevant to the 30-day recency gate, the only arithmetic consumer.
- `/activity`: cursor paging in; **depth caps unchanged** (`activity_max_pages=10 × 500` and `closed_max` stay),
  so grounded verdicts/ROIs cannot shift silently on restore. The stale "venue wall" comment at
  `loss_grounding.py:149` now says the cap is **ours** (v2's cursor walks to exhaustion; deepening is a separate
  decision). Non-TRADE activity types (REDEEM/REWARD/MAKER_REBATE, empty side) confirmed present in v2 and
  already filtered by the TRADE+SELL requirement (`live_driver.py:653-655`).

## §4 Failure is loud — and no caller can read a failure as an empty book

Client: every hard failure raises a typed error the callers already handle AND emits the rate-limited
`HARD-FAIL` ERROR line (tested: 410, 429-exhausted, 5xx-exhausted, timeout, malformed envelope, plus the
suppression counter itself). Callers, each verified to treat a raise as *failure*, never as an empty book:
`live_driver.py:1440-1443` per-whale skip, prior snapshot kept (and `:1460-1465` exit-confirm failure keeps the
snapshot); `ingest.py:228-235` retry→raise→wallet FAILS (isolated, never PARTIAL); `ingest.py:304-305` the
DELETE of stored open rows happens only **after** a successful fetch; `search_run.py:226-231` per-wallet
try/except; `paper.poll_pinned` leaves the whale un-polled (Ruling G); pm_web Analyze/Refresh propagate to an
HTTP error; `fetch_market_resolutions` keeps its chunk-skip + `not_found` sentinels (gamma, unchanged).

## §5 Box verification (read-only; scratch; live tree untouched)

- **A/B pytest** (BASE files vs TARGET files, same suite, box venv, `-p no:pytest_ethereum
  --continue-on-collection-errors`): **34 failures in A, 34 in B, full-list diff B-only = ∅, A-only = ∅ — zero
  regressions.** TARGET full run: **792 passed / 34 failed (69.8 s)**; all 34 are pre-existing, identical in
  BASE, concentrated in pm_web view/env-dependent files (`test_live_r3` 14, `test_polymarket_watch_only_sort` 9,
  `test_polymarket_per_division_wallets` 3, `test_accounts_m2` 3, `test_stage2_phase3` 2, `test_stage2_nav` 2,
  `test_rung3_observability` 1). `tests/test_backtest_polymarket_arbitrage.py` is excluded: it loads
  `scripts/backtest_polymarket_arbitrage.py`, which does not exist on the prod-live lineage (verified) — it can
  never collect here, in A or B. **All new tests passed** (migration suite + 16 matcher tests). `py_compile` +
  import closure green (and noted: those alone prove almost nothing — the 792 tests call the real functions; every
  new log statement's arguments were checked by eye).
- **Live v2, real wallets** (`analysis/pv2_live_verify.py`; **118 public GET calls actual** vs ~90 estimated —
  the 7,238-row book cost 15 pages; throttled ~2/s, zero 429s, `errors: []`): all **42 attached wallets'** books
  fetched **complete**, including the ITF whale (16 rows) and the multi-page monsters (7,238 rows / 15 cursor
  pages — live proof of the cursor walk). Closed-row parity + partial-sale identity as in §3. Leaderboard
  `category=Sports` works. Activity 100 rows, seconds-range true.
- **Signal-side dry-run** (the client's blast surface, per (account, category) from active attachments):
  **jack 777 raw entry signals across 18 non-zero pairs (top: bun 210, mex/sea/bra 73, cfb 50, f1 44); karen 495
  / marc 495 / trey 494 across 10-11 pairs** (shared whale books); 50 of 73 attachment pairs non-zero right now.
  Samples: `ukr1-che-met-2026-11-01-met|No|idx1`, `boxing-chabot-romero-2026-10-08|Thomas Chabot|idx0`. These
  are raw whole-book signals: at run time most die at the category matcher (wrong-category slug), idempotency
  (already copied pre-outage), dead Kalshi markets, or the caps — and marc/trey place nothing (latched).
  **The full arm-gated evaluate was NOT driven:** it needs the engine's authenticated Kalshi catalog/service
  env, which scratch does not have — per the order, not improvised. The matcher layer is proven offline (§2).

## §6 Staged deploy + the written prediction

**Runners** (authored + validated, NOT run — deploy and restart are Jack's):
- `analysis/pv2_graft.{ps1,sh}` — tar+scp the 2 files → stage-gate (staged==TARGET) → **drift-gate
  (box==BASE)** → backup to `/home/azureuser/backups/pv2_<ts>/` (kept) → apply → verify live==TARGET →
  py_compile + import closure → **rollback-all to BASE on any failure, re-verified**.
- `analysis/pv2_postverify.{ps1,sh}` — read-only: PIDs changed + ActiveEnter later than file mtime (both
  services); live==TARGET; journal since restart: **zero** v1-410s, v2 success lines growing, zero HARD-FAIL,
  429 count; boot-reconcile clean ×4 + latch grouping unchanged + `arm:global`; heartbeats fresh incl.
  `jack:nba` back in the driven set; first fills with `leg_audit`; cash snapshots updating.
- **What restarts:** `trading-corp` (engine: live_driver `/positions`+`/activity`) AND `prediction-markets-web`
  (Analyze/Refresh). `pm_cli` + any one-shot scripts pick the change up on their next invocation — no deploy.

**PREDICTION (written before any restart; per account, given current arm + attestation state):**
1. Both services come up active; engine MainPID ≠ 809209, pm_web MainPID ≠ 698062; NRestarts 0.
2. Journal: **zero** "Data API v1 has been retired"; `polymarket-data-api … rows in …ms` INFO lines flowing;
   zero HARD-FAIL; at most a few 429 warnings at peak fan-out.
3. Boot-reconcile **clean on all four** (books flat — re-verified today; portfolio reads work even on the
   dead-attestation accounts, proven by their 5-minute balance snapshots). **No new latch at boot.**
4. Arm state identical pre/post: global armed; jack 18 armed + 8 latched (boot_reconcile 2026-10-06); karen
   16 + 6; **marc 22 latched auth_failure (10-28) and trey 22 latched auth_failure (10-22) — they STAY latched**
   until the re-arm runner runs; they evaluate signals and place nothing.
5. `jack:nba` re-enters the driven set at this restart (2 active attachments, re-derived at task spawn) — its
   heartbeat goes fresh for the first time since 10-23; still disarmed, so it cannot place.
6. Signals: n_signals > 0 within minutes on pairs with live whale books (§5 counts). **jack + karen are ARMED:**
   placements resume as soon as a signal matches a live market and passes the gates — including the backlog of
   whale positions opened during the 15-day outage that were never copied (idempotency has no rows for them),
   bounded by per-order $50 / sub-day $150 / account-day $150 / exposure $350 / 50-orders caps. Two sub-cases:
   **(a)** attestation valid → first fills appear, each carrying `leg_audit`; **(b)** attestation lapsed (likely
   per the trey→marc ~6-day stagger) → the FIRST placement 403s → account-wide `auth_failure` latch on that
   account — loud, harmless on a flat book, and exactly the checklist item 1 warning. If you do not want
   immediate resumption on jack/karen at restart, `live-disarm` them first (manual, freely re-armable) — your
   call, not mine.
7. Settlement scans, shard snapshots, and the Kalshi side generally: unchanged, keep running.

## §7 Jack's pre-restart checklist — live values (probe 2026-11-17 13:24 UTC)

1. **Attestation — renew all four in the Kalshi UI first.** jack + karen are still ARMED: if either attestation
   lapsed, their first order after v2 goes live 403s and latches the whole account (prediction 6b). Last
   proof-of-life fills: jack 11-08 06:37, karen 11-03 06:01, marc 10-28 05:41, trey 10-23 03:34. The engine
   cannot read expiries — kalshi.com → Account & security → API Keys.
2. **Book flat — re-verified at probe time:** zero open legs (engine `_HELD_SQL` grain, no rows);
   `ct_entered == ct_exited` exactly: jack 9690/9690, karen 6355/6355, marc 1562/1562, trey 1451/1451. Nothing
   has opened since the scope report.
3. **Attachments:** **no armed category has zero active attachments** (full armed×attachment join in the probe
   output) — the "zero-attachments-at-boot is not driven until the next restart" gap currently bites nothing
   that is armed. FYI: `jack:nba` (latched) has 2 attachments and will be driven-but-disarmed after the restart.
4. **Contract sizes (change nothing — reported as asked):** per-sub `contracts` sizing with caps $50/order,
   $150/sub-day, $350 open, 50 orders/day. jack runs the largest (itf 12, nfl 10, lal 10, wta 10, mls 7);
   karen mid (mlb 8, itf 7, lal 7, mls 7); marc/trey mostly 1–2 (itf 5). The **2026-10-22 backtest finished**
   (branch `pm-backtest-2026-10-22`, worktree `cc-pm-backtest-2026-10-22-wt`, `00_REPORT.md` + analysis/):
   verdict — no copyable edge beats the fee (T1 net −$369.22, fees 67.8 % of loss; ITF the lone positive at
   ≤$50/signal; size INVERTED — small quartile +$195 vs big −$237). It argues for smaller, not larger; sizes
   above left untouched.
5. **Other divisions (the restart bounces all; one line each, recommending nothing):** MACE — 8 open + 5
   `closing` rungs (the 5 are the known spot=0.0-parked set, intact at RH; its own audit governs restart timing,
   incl. RH pickle validity at boot). PEAD — only an archive table visible in the legacy DB; its own chat
   checks. PMCC — no open-state table in the legacy DB surface probed; its own chat checks. bitunix —
   observer/research state tables only; its own chat checks.

## §8 Exact commands for Jack (in order; each one line — DO NOT run any as an agent)

1. Deploy (graft, gated, rollback-all): `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pv2_graft.ps1"`
2. Restart engine (canonical): `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"`
3. Restart pm_web (your az channel, single-line, no embedded quotes inside --scripts):
   `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "systemctl restart prediction-markets-web"`
4. Verify (read-only): `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pv2_postverify.ps1"`
5. FF `prod-live` + tag + push, after a green verify (from `C:\Users\AA Incorporado\cc`):
   - `git push origin pm-polyv2-migration-2026-11-17`
   - `git push origin pm-polyv2-migration-2026-11-17:prod-live`
   - `git tag pv2-deploy-2026-11-17 75dbd048` *(use the branch tip at deploy time if later commits land)*
   - `git push origin pv2-deploy-2026-11-17`
6. Afterwards: append the deploy_log entry (draft in `analysis/pv2_deploy_log_draft.md`) and, when you choose,
   run the re-arm runner (`pm_rearm.ps1`) with your list — preconditions: attestation renewed + this deploy live.

## Compliance + session log

Read-only disclosure: agent SSH occurred and was read-only throughout (sqlite `mode=ro`, `systemctl show`,
`journalctl`, sha/stat, public unauthenticated v2 GETs: 8 probe calls + 6 pagination-probe calls + 118
live-verify calls, throttled, zero 429s); box-scratch work wrote only under `/home/azureuser/pv2_scratch`
(cleaned at close) and `/home/azureuser/pv2_*.tgz` (cleaned); the live tree, engine, services and every DB were
untouched; no credential was read, printed or written; backups directory untouched. Runner inventory (all
ASCII-validated, parse-checked): `pv2_base`, `pv2_probe2`, `pv2_stage`+`pv2_run1`, `pv2_live_verify.py`+
`pv2_liveexec`, `pv2_checklist`, and the NOT-RUN `pv2_graft` / `pv2_postverify`. One instrument self-catch:
run-1's sha-gate correctly caught a stale recorded test-file sha (post-edit); `-qq` (pyproject `-q` + mine)
suppressed pytest's counts line, re-run plain for the real numbers. 34-failure known-stale set recorded above.
