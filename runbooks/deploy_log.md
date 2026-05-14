# Trading Corp — Production Deploy Log

**Purpose.** Append-only record of every production deploy. The
prod VM has no git, so this file is the single source of truth for
"what's running on prod right now."

**Why this exists.** Recurring failure mode pre-2026-05-02: forgetting
that a feature already shipped (because it was bundled in a bulk-track
commit, or scaffolded forward-compat in an earlier phase, or
implemented before the BACKLOG.md item was retired). The fix is
captured in CLAUDE.md §1 — "Before any deploy-adjacent work" — and
this log is the artifact that makes it possible.

**Source of truth precedence:**
1. `runbooks/deploy_log.md` (this file) — what's on prod right now
2. md5-diff between local and prod — verify before deploying
3. `BACKLOG.md` — what we want to do, NOT what's done
4. Memory entries — same caveat as BACKLOG.md

---

## Template for new entries

```markdown
## YYYY-MM-DD HH:MM UTC — <phase or feature label>

**Commits:** <commit-hashes>
**Triggered by:** <user-request or session-context>
**Backup tag:** `.pre-<label>-YYYYMMDD-HHMM` (or `n/a` for first-shipment of new files)

**Files deployed (N):**
- `<path>` — <one-line summary of change>

**Features shipped (load-bearing for future "is X done?" checks):**
- <feature 1>: <what's now live, observable how>
- <feature 2>: <...>

**Notable code changes (callouts a future Claude shouldn't miss):**
- <change>: <where it lives, why it matters>

**Latent bugs caught + fixed (if any):**
- <bug>: <symptom, fix, where>

**Verification:**
- <PID change, audit row landing, dashboard probe, etc.>

**Inert / dormant on current traffic (if any):**
- <code that's deployed but not exercising — why, and what would trigger it>

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=<backup-tag>; BASE=/home/azureuser/trading_corp; \
for f in <list>; do mv \$BASE/\$f.\$TAG \$BASE/\$f; done; \
rm -rf <new-files-or-dirs>
"
```
```

---

## 2026-05-12 03:45 UTC — Copy-trader exit pairing + K3 price capture + dashboard polish

**Triggered by:** Jack flagged from screenshots: (1) Kalshi dashboard "not legible" — ENTRY column showed $0.000 / SIGNAL & RESOLVES empty for every K3 row, (2) copy-trader exits visible in Telegram but never closed/PnL'd on the dashboard. Two independent issues, both architectural.

**Files deployed (7 modified, 3 backup tags across 3 sub-deploys):**

### Deploy 1 (03:33 UTC, tag `pre-exit-pairing-d1-20260512-0333`): tracked-file patches
- `trading_corp/persistence/db.py`:
  - `polymarket_round_trips` + `kalshi_round_trips` schemas + idempotent migration both get a new `entry_order_id TEXT` column. Indexes `ix_*_entry_order_id` (partial: WHERE NOT NULL).
- `trading_corp/main.py`:
  - K3 `_scheduled_kalshi_copy_trader_loop` base_payload allowlist gets `whale_entry_price` + `whale_exit_price` (memory `trading_corp_audit_payload_allowlist` — without this the new K3 fields silently drop).
- `trading_corp/web/data.py`:
  - `PMRoundTrip` + `PMOpenTrade` dataclasses gain `whale_handle` field (and `side_detection_confidence` on PMOpenTrade).
  - `_query_pm_round_trips` Polymarket branch reads `extra_json` + overrides `market_result='whale_closed'` when present.
  - `_query_pm_open_trades` both branches: add `side='buy'` filter (so SELL audit rows render as History, not Open) + exclude rows linked as `entry_order_id` on a paired round-trip.
  - `_query_pm_pending_count` both branches: same exclusion as Open.
  - `whale_handle` populated from `whale_user_name` (PM) / `whale_handle` (K3) payload keys.
  - Earlier `arb_type` copy_trade clause was already-applied (this morning's deploy) — patcher detected and skipped idempotently.

### Deploy 2 (03:34–03:36 UTC, tag `pre-exit-pairing-d2-20260512-*`): untracked-file transfers
Per-file base64 transfers (one 131KB combined script silently aborted in az — script size limit; split into 4 per-file calls of ~30KB each succeeded):
- `trading_corp/agents/strategies/kalshi_copy_trader.py`:
  - `_detect_side` now returns `(side, confidence, price)` — captures the matched trade's price (yes_price_dollars or no_price_dollars based on taker_side).
  - `_emit_entry` sets `limit_price=entry_price` + adds `whale_entry_price` to extra. Entry rationale includes `@ $X.XX`.
  - `_emit_exit` becomes **async**, accepts `quote_fetcher` (the trade-tape KalshiBroker), calls `broker.quote(ticker)` for exit price. For NO holdings, inverts: `exit_price = 1 - yes_mid`. Adds `whale_exit_price` to extra. Exit rationale includes `@ $X.XX`.
  - Per-whale snapshot stores `entry_price` so exits can carry it through.
  - New helper `_trade_price_for_side`.
  - Tests updated: `_detect_side` return-tuple now triple instead of pair; assertions added for price.
- `trading_corp/agents/polymarket_resolver.py`:
  - New `_pair_pending_exits(db_url)` — pure SQL, no broker calls. Matches SELL audit rows from `polymarket_copy_trader` to most-recent prior BUY by `(whale_wallet, condition_id, outcome_index)`, computes `realized_pnl = qty × (exit_price − entry_price)`, inserts round-trip keyed by SELL's order_id with `entry_order_id` linking back to BUY.
  - `_fetch_unresolved_orders` gets `side='buy'` filter + `entry_order_id NOT IN` exclusion so SELLs and paired BUYs aren't re-scanned by the market-settle path.
  - `resolve_pending_round_trips` runs pairing FIRST (pure SQL), then market-settle (gamma-api calls). Counts include `paired`, `pair_scanned`, etc.
- `trading_corp/agents/kalshi_resolver.py`:
  - Parallel `_pair_pending_exits` matching on `(whale_handle, ticker, outcome)`.
  - Same `side='buy'` + entry_order_id exclusion in `_fetch_unresolved_orders`.
  - Wired into `resolve_pending_round_trips` same way as Polymarket.
- `trading_corp/web/templates/partials/pm_dashboard_body.html`:
  - ENTRY column: render `—` (muted) when entry_price is None/0, else `$X.XXX`.
  - SIGNAL column: prioritize whale_handle (`@name` with side_detection_confidence) over divergence_pct/edge_cents for copy_trader rows.
  - History tab market_result: render `whale exit` badge (warn color) when `market_result == 'whale_closed'`.

### Deploy 3 (03:45 UTC, tag `pre-k3-pair-relax-20260512-0345`): K3 pre-existing-row pairing relax
- `trading_corp/agents/kalshi_resolver.py`: relaxed K3 pairing to NOT skip on `exit_price <= 0`. Pre-Fix-A K3 audit rows had `limit_price: null`; this lets the 73 stranded historical exits pair into round-trips with `realized_pnl=0` so they show up in History tab. Going forward, K3 rows have real prices and produce real PnL.

**Features shipped:**
- **K3 dashboard now renders legibly.** ENTRY column shows real prices going forward; SIGNAL column shows `@whale_handle` + confidence; `whale exit` badge surfaces in History tab.
- **Copy-trader EXITs now close round-trips.** Both venues. 73 paired K3 round-trips landed immediately on first tick; 1 PM round-trip paired (+$0.20 realized). New exits going forward pair on the next resolver tick (hourly default).
- **Schema additions are forward-compat.** entry_order_id NULL on all legacy/market-settle rows; only SET on paired whale-closed rows.

**Notable code changes (callouts a future Claude shouldn't miss):**
- The resolver pairing path runs BEFORE the market-settle path on every tick — pure SQL, no API cost.
- `_fetch_unresolved_orders` in BOTH resolvers now filters `side='buy'` AND excludes audit rows in `entry_order_id`. Any new strategy that emits SELL audit rows MUST be aware that those don't auto-resolve via market-settle anymore.
- Dashboard's "whale exit" badge appears when `market_result == 'whale_closed'`. Future round-trip resolvers can use the same sentinel to mark non-settlement closes.
- The K3 strategy's `_emit_exit` is now ASYNC. Any caller has to `await`.

**Latent bugs caught + fixed:**
- K3 entry rationale used to say `opened N contracts` with no price; now includes `@ $X.XX` parsed from trade tape. Same for exits (was using copy_size_usd as if it were a price).
- K3 NO-side exits previously had no price source at all; broker.quote() returns YES mid, so the code inverts to `1 - yes_mid` for NO holdings.

**Verification:**
- All 137 PM-dashboard + resolver + copy_trader tests pass locally (pytest passing — 8 unrelated failures in PMCC date-drift + webhook _Deps fixture are pre-existing).
- Post-deploy 3, K3 dashboard at `/prediction-markets/kalshi_copy_trading`: 15 open rows + 147 history rows (73 paired round-trips × main+expand) + 73 "whale exit" badges + @smedtoshi/@tom14cat14 SIGNAL renders.
- PM resolver tick log: `paired: 0, pair_scanned: 0` (no new PM exits to pair beyond the +$0.20 one from earlier; whales still holding).
- 1 PM whale-closed round-trip with realized_pnl=+$0.20 (real, prices were captured day-1 on PM side).

**Inert / dormant on current traffic:**
- The 73 K3 historical pairings show `realized_pnl=0` — accurate given missing pre-Fix-A prices. New K3 round-trips going forward will have real PnL.
- `whale_handle` field on PMRoundTrip is None for legacy market-settle rows; populated only for whale-closed rows. Template handles None gracefully.

**Rollback recipe:**
```bash
# Three layers (most recent first):
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript \
  --scripts "TS=20260512; BASE=/home/azureuser/trading_corp; \
    # Layer 3 (pair-relax) rollback:
    mv \$BASE/trading_corp/agents/kalshi_resolver.py.pre-k3-pair-relax-\${TS}-0345 \$BASE/trading_corp/agents/kalshi_resolver.py 2>/dev/null; \
    # Layer 2 (untracked file transfers) rollback:
    for f in trading_corp/agents/strategies/kalshi_copy_trader.py trading_corp/agents/polymarket_resolver.py trading_corp/agents/kalshi_resolver.py trading_corp/web/templates/partials/pm_dashboard_body.html; do \
      BACKUP=\$(ls \$BASE/\$f.pre-exit-pairing-d2-\${TS}-* 2>/dev/null | head -1); \
      [ -n \"\$BACKUP\" ] && mv \"\$BACKUP\" \"\$BASE/\$f\"; \
    done; \
    # Layer 1 (tracked file patches) rollback:
    mv \$BASE/trading_corp/persistence/db.py.pre-exit-pairing-d1-\${TS}-0333 \$BASE/trading_corp/persistence/db.py; \
    mv \$BASE/trading_corp/main.py.pre-exit-pairing-d1-\${TS}-0333 \$BASE/trading_corp/main.py; \
    mv \$BASE/trading_corp/web/data.py.pre-exit-pairing-d1-\${TS}-0333 \$BASE/trading_corp/web/data.py; \
    sudo systemctl restart trading-corp.service" \
  --query "value[0].message" -o tsv
# Note: entry_order_id column stays after rollback (sqlite ALTER not reversible without table rebuild).
# Old code doesn't reference it so no harm — just unused column on existing rows.
```

---

## 2026-05-12 02:34 UTC — K3 throttle to fit Apify Starter $200/mo hard cap

**Triggered by:** Session-start Apify probe revealed Starter plan burn at $10.68/day (= ~$320/mo extrapolated) — would exhaust in ~1.6 days. Jack clarified Apify Starter is hard-capped at $200/mo (no plan upgrade), asked me to cut data-request volume to fit.

**Files deployed (1 config, no code, no restart):**
- `config/strategies.yaml`: `kalshi_copy_trader.poll_interval_sec` 300s → **600s** (5min → 10min cadence). Single-line config change, hot-reloaded via `KalshiCopyTraderAgent._reload` mtime check on next cycle.

**Backup tag:** `pre-k3-throttle-20260512-0234` (yaml-only, single file).

**Math:**
- K3 makes exactly 1 Apify call per cycle (`fetch_open_positions` with all 4 whales batched in one actor run), so cost scales linearly with cadence.
- 5min → 10min halves request volume → ~$5.34/day → **~$160/mo** (Apify Starter cap = $200/mo, leaving ~$40/mo buffer for spikes or future whale-pool expansion).
- 8min (480s) would land right at $200/mo with zero buffer — too tight; 10min chosen for safety.

**Notable behavior change:**
- K3 position-freshness lag becomes 10min worst-case (was 5min). Per the strategy's `positions don't change fast on Kalshi` design assumption, this is fine — biggest theoretical loss is missing a fast whale entry/exit within a single 10min window, vs. observed 5min.

**Pre-existing memory now stale (separate update made):**
- `trading_corp_kalshi.md` had "Cost: ~$30-50/mo expected" — actual measured $320/mo at 5min/4-whale (off by ~10x). Memory updated to reflect measured cost + $200 cap + new 10min cadence.

**Yaml drift caught (note for future deploys):**
- Patch script's primary string-match fell through to the line-only regex fallback — prod's `config/strategies.yaml` had a slightly different comment on the K3 `poll_interval_sec: 300` line than my local. Fallback regex correctly rewrote just the line. Same `trading_corp_prod_git_drift` pattern as the data.py deploy earlier this session.

**Verification:**
- Backup created (`pre-k3-throttle-20260512-0234`), yaml re-parses cleanly, `poll_interval_sec` confirmed = 600 via `yaml.safe_load`.
- No systemd restart — mtime hot-reload picks up on next K3 reload cycle (within current 5min sleep window).
- TODO: re-probe `/v2/users/me/usage/monthly` after 24h to confirm new daily burn ≈ $5.34 (50% of pre-throttle). Cumulative cycle burn at next check should grow by ~$5.34 between check times.

**Rollback recipe:**
```bash
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript \
  --scripts "TAG=pre-k3-throttle-20260512-0234; BASE=/home/azureuser/trading_corp; \
    mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml" \
  --query "value[0].message" -o tsv
# No restart needed for rollback either — mtime hot-reload picks up.
```

---

## 2026-05-12 02:19 UTC — PM dashboard: render copy_trading divisions

**Triggered by:** State-check at session start revealed `/prediction-markets/kalshi_copy_trading` and `/prediction-markets/polymarket_copy_trading` were rendering empty despite K3 (110 audit rows) and Polymarket Copy Trader (54 audit rows) already firing live. Two independent gaps:

1. **Kalshi resolver wiring gap.** `kalshi_resolver._KALSHI_ACTORS` hardcoded the 3 arb-family actors and excluded `kalshi_copy_trader`, so K3 audit rows could never become `kalshi_round_trips`. Polymarket resolver was already wired correctly (memory `trading_corp_polymarket` 2026-05-11 deploy).
2. **Dashboard data-layer queries didn't know about copy_traders.** `web/data.py`'s 4 PM query functions hardcoded `actor='polymarket_arbitrage'` / the 3-actor Kalshi list, and hardcoded `division='polymarket_arbitrage'` on output rows — so even with a divisions.yaml slug, queries returned zero.

**Files deployed (2 modified, 1 backup tag):**

**Deploy (02:15 UTC, tag `pre-pm-dashboard-copy-20260512-0215`):**
- `trading_corp/agents/kalshi_resolver.py`:
  - Added `kalshi_copy_trader` to `_KALSHI_ACTORS`, `_KALSHI_DIVISIONS`, `_ACTOR_TO_DIVISION` (→ `kalshi_copy_trading`), `_ACTOR_TO_ARB_TYPE_DEFAULT` (→ `copy_trade`).
  - `_detect_side` needed no change — K3 payload's `outcome` field is `"yes"/"no"` which it already handles.
  - md5 matched local exactly (`618ed95f…`) — prod/local in sync on this file.
- `trading_corp/web/data.py` (4 functions touched, 7 string-replace edits):
  - `_query_pm_round_trips` Polymarket branch: read `division` column from `polymarket_round_trips` via `COALESCE(division, 'polymarket_arbitrage')` so legacy pre-column rows still filter as arbitrage; accept any `polymarket_*` slug.
  - `_query_pm_open_trades` Polymarket branch: actor list expanded to `('polymarket_arbitrage', 'polymarket_copy_trader')`; filter by `payload.division` so single-division view doesn't bleed cross-division rows.
  - Open-trades title fallback chain extended to read `p.get("market_title")` (the copy_trader payload uses that key; arbitrage uses `market_question`).
  - `_query_pm_open_trades` Kalshi branch: actor list expanded to include `kalshi_copy_trader`; arb_type derivation gets `copy_trade` clause.
  - `_query_pm_pending_count`: both branches mirror the open-trades fixes.
  - `_query_pm_equity_curve` Polymarket branch: switched to IN-clause for forward-compat (when polymarket_copy_trading equity_history rows eventually land they'll auto-render — today there are zero).
  - Post-patch md5 was `815e1bb8…` ≠ local `3be4eb01…`. Drift is in non-edited regions of data.py — patches applied cleanly (all 7 old_strings matched uniquely) so my edits are correctly in place; the divergence is preserved (no stomp). This is the `trading_corp_prod_git_drift` pattern, expected.

**Features shipped (load-bearing for future "is X done?" checks):**
- `/prediction-markets/kalshi_copy_trading` Open tab renders the 110 paper copies (verified 233 `<tr>` in Open tab = 110 trades × main+expand rows + headers).
- `/prediction-markets/polymarket_copy_trading` Open tab renders the 54 paper copies (verified 121 `<tr>` rows similarly).
- Kalshi resolver will now convert K3 `would_have_placed` audit rows to `kalshi_round_trips` rows on its hourly tick. First batch lands ~03:19 UTC; resolutions appear in the dashboard's History tab as they accumulate.

**Notable code changes (callouts a future Claude shouldn't miss):**
- `kalshi_resolver._KALSHI_ACTORS` is now 4 entries, not 3. Any future Kalshi strategy MUST be added here AND to `_ACTOR_TO_DIVISION` AND to the actor list in `web/data.py:_query_pm_open_trades` (Kalshi branch line ~2629/2646) AND `_query_pm_pending_count`. Same applies for Polymarket — add to actor list in the same two functions' Polymarket branches.
- `polymarket_round_trips.division` column is now LIVE both at write-time (resolver line 92-98) and read-time (data.py uses COALESCE for legacy NULL rows).

**Latent bugs caught + fixed (none new):** none. The Apify burn at $10.68/day (44% of $29 cap in 26h, ~1.6 days to exhaustion) remains an OPEN URGENT item — not addressed in this deploy. Whales tab P0a + multi-leg resolver P0b deferred per scope agreement.

**Verification:**
- Patch markers: `grep -c 'kalshi_copy_trader' trading_corp/agents/kalshi_resolver.py` = 3 ✓; `grep -c 'polymarket_copy_trader' trading_corp/web/data.py` = 5 ✓; `grep -c 'kalshi_copy_trader' trading_corp/web/data.py` = 3 ✓.
- Service restart: PID rotated, `systemctl is-active` = `active`, no `ERROR|Traceback|ImportError` in startup log.
- Dashboard probes via localhost:8000 (bypasses Authelia):
  - kalshi_copy_trading partial: 200 OK, 436KB, 233 `<tr>` in Open tab, KX* tickers present.
  - polymarket_copy_trading partial: 200 OK, 214KB, 121 `<tr>` in Open tab.
  - Regression check on existing PM divisions all clean: polymarket_arbitrage (55 open / 5 history), kalshi_llm_arbitrage (401 open / 59 history), kalshi_arbitrage (133 open / 0 history).

**Inert / dormant on current traffic (if any):**
- `polymarket_copy_trading` equity curve will be empty until equity-snapshot loops are spawned for the copy_trading divisions (currently zero rows in `polymarket_equity_history` and `kalshi_equity_history` for those divisions). Forward-compat IN-clause is already in place; just need an orchestrator change to start the snapshot loops. Not blocking dashboard utility.
- `copy_trade` arb_type label appears in `_query_pm_open_trades` but template UI may render it as plain text; no special CSS treatment yet.

**Deploy script gotcha for next time:**
- The deploy script `runbooks/.deploy_pm_dashboard_copy_trades.sh` had `set -euo pipefail` at the top, which caused bash to exit immediately when the Python heredoc exited non-zero (on the soft md5-mismatch signal) — BEFORE running the rollback / restart blocks. Net effect: first run silently applied patches but didn't restart systemd. Second run reported "NOT FOUND" because prod already had the patches. Workaround: dropped the rollback-on-mismatch (distinguished hard vs soft failures) and re-ran the restart manually. Future deploy scripts should either replace `set -e` with explicit error handling, or use `python3 ... || true` and check `$?` explicitly.

**Rollback recipe:**
```bash
# SSH path (blocked from current IP — use az alternative below):
ssh azureuser@trading.jacksumner.com "
TAG=pre-pm-dashboard-copy-20260512-0215; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/kalshi_resolver.py.\$TAG \$BASE/trading_corp/agents/kalshi_resolver.py; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
sudo systemctl restart trading-corp.service
"

# az alternative:
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm \
  --command-id RunShellScript \
  --scripts "TAG=pre-pm-dashboard-copy-20260512-0215; BASE=/home/azureuser/trading_corp; \
    mv \$BASE/trading_corp/agents/kalshi_resolver.py.\$TAG \$BASE/trading_corp/agents/kalshi_resolver.py; \
    mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
    sudo systemctl restart trading-corp.service" \
  --query "value[0].message" -o tsv
```

---

## 2026-05-11 23:00 UTC — paper_trade_replay: BitUnix symbol routing + premature-expired fix

**Triggered by:** Board asked when the "Paper-trade win rate" panel on `/division/bitunix_futures` would populate. Investigation found two bugs:

1. **BitUnix paper trades never resolved.** The replay loop was running every 15min but failing on every BitUnix order with `ERROR: coinbase does not have market symbol BTC/USDT.P`. Root cause: single-venue Coinbase ccxt fetcher used for ALL strategies.
2. **Premature `expired` classification.** Once #1 was fixed and BitUnix bars started flowing, all 4 stuck rows immediately got marked `expired` — but the trades were only 2-6h old with a 24h `max_hold_seconds`. The classifier was treating "ran out of fetched bars" as "trade expired" without checking wall-clock elapsed time.

**Files deployed (1 modified, 2 backup tags):**

**Deploy 1 (22:30 UTC, tag `pre-replay-bitunix-routing-20260511-2230`):** symbol-aware OHLCV router.
- `trading_corp/agents/paper_trade_replay.py`:
  - Renamed `_default_ccxt_fetcher` → `_coinbase_ccxt_fetcher` for clarity.
  - **New `_bitunix_kline_fetcher`** — hits `https://fapi.bitunix.com/api/v1/futures/market/kline` (no auth, same source `LiveBarCache` uses for live ATR). Paginates 1000 bars/call. Returns ccxt-shaped `[ts_ms, o, h, l, c, v]` rows in chronological order.
  - **New `_to_bitunix_symbol` / `_is_bitunix_symbol`** helpers. Detection rule: symbol ends in `.P` → BitUnix perp; else → Coinbase spot. Symbol normalization: `BTC/USDT.P` → `BTCUSDT` for the REST call.
  - **New `_default_router_fetcher`** — single entry point that dispatches per-symbol. Replaced `_default_ccxt_fetcher` reference in `_replay_tick_async`.
  - Smoke-tested against live BitUnix API: 30×1m bars returned chronologically with sane OHLCV.

**Deploy 2 (23:00 UTC, tag `pre-replay-still-open-20260511-2300`):** still_open verdict.
- `trading_corp/agents/paper_trade_replay.py`:
  - **New `_Resolved.result` value: `"still_open"`** — transient verdict the caller never writes to DB. Documented in the docstring as "row stays at result=NULL so the next replay tick picks it up again."
  - `_classify` now computes `elapsed = now - row.ts` and only returns `"expired"` when `elapsed >= max_hold_seconds`. Otherwise returns `"still_open"` (no DB write).
  - Helper `_parse_row_ts(ts)` for the wall-clock comparison.
  - `_replay_tick_async` checks for `result == "still_open"` and `continue`s past `_update_row` — leaves row at NULL for the next tick.
  - New `still_open` bucket in the counts dict for visibility.
- **DB cleanup step:** UPDATEd 4 prematurely-expired BitUnix rows back to `result=NULL, result_ts=NULL, result_price=NULL, actual_pnl_dollars=NULL, actual_r_multiple=NULL, bars_to_resolution=NULL` so they re-process correctly under the fixed classifier.

**Verification (immediately post-deploy):**
- Post-restart catch-up tick: `{'scanned': 4, 'resolved_win': 0, 'resolved_loss': 0, 'resolved_expired': 0, 'still_open': 4, 'errors': 0}` ✓
- All 4 BitUnix rows back to `result=NULL` — will re-evaluate every 15min until either TP/SL hits OR the genuine 24h max_hold elapses.

**4 boundary cases unit-tested locally:**
- 2h old, 24h hold, no hit → `still_open` ✓
- 25h old, 24h hold, no hit → `expired` ✓
- 2h old, TP hit at bar 60 → `win` (bars_to_resolution=61) ✓
- 2h old, SL hit at bar 30 → `loss` (bars_to_resolution=31) ✓

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-replay-still-open-20260511-2300; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/paper_trade_replay.py.\$TAG \$BASE/trading_corp/agents/paper_trade_replay.py; \
sudo systemctl restart trading-corp.service
"
```
(Rolls back BOTH fixes — the still_open verdict ride on top of the symbol-routing change. To partially roll back just the still_open fix, use `pre-replay-bitunix-routing-20260511-2230` instead.)

---

## 2026-05-11 21:20–22:00 UTC — Dashboard timezone sweep (all timestamps → Eastern)

**Triggered by:** Board said "the bitunix dashboard you helped me with…it is showing times in zulu time. i need all times on the dashboard to be eastern timezone." Subsequent sweep across all dashboard surfaces to replace UTC literals with ET.

**Approach:** Jinja filters `et_hms` / `et_short` / `et_full` already existed (registered in `web/app.py:94-96`, sourced from `utils/time.py`). Just needed to swap raw timestamp slices for filter calls — no data builder changes for most, one targeted addition for the BitUnix score panel.

**Files deployed (3 modified across 2 sub-deploys, backup tag `pre-tz-sweep-20260511-2145` + `pre-tz-sweep-routes-20260511-2200`):**

**21:20 UTC deploy — BitUnix score panel + four other templates:**
- `trading_corp/web/data.py` — added `ts_et` field via `format_et_short()` to each `recent_evals` + `recent_fires` entry in `build_bitunix_score_view`. (Pre-formatting in the builder keeps the template branchless and ensures consistency.)
- `trading_corp/web/templates/partials/bitunix_score_panel.html` — Recent fires + Recent evaluations tables now render `{{ f.ts_et }}` / `{{ e.ts_et }}` (was `{{ f.ts[5:16] }}Z` etc).
- `trading_corp/web/templates/base.html` — scheduler last_run header: `{{ snap.health.scheduler.last_run | et_hms }}` (was `[11:19]Z`).
- `trading_corp/web/templates/partials/kalshi_analysis.html` — position `expires_at` uses `| et_short` filter.
- `trading_corp/web/templates/partials/polymarket_analysis.html` — event `resolves_at` uses `| et_short` filter.
- `trading_corp/web/templates/research.html` — 5 occurrences of `ts[:19]` → `(ts | et_short)`.

**22:00 UTC deploy — routes.py renderers:**
- `trading_corp/web/routes.py` — 3 inline `ts_dt.strftime("%Y-%m-%d %H:%M:%S UTC")` calls (PMCC analysis renderers) replaced with `format_et_full(ts_dt)`. Import already present at line 33.

**Verification (post-deploy):**
- Scheduler header now reads `sched: 08:33:07 ET` (was `12:33:07Z`).
- BitUnix score panel Recent fires + Recent evaluations tables render `05-11 15:54 ET` (was `05-11T19:54Z`).
- Final grep sweep confirmed no remaining `}}Z`, no remaining `[:19]` raw slices, no remaining `"UTC"` literals across `trading_corp/web/`.

**Inert / dormant:**
- The two `_humanize_ts` callers in `data.py` (used for activity-feed "5m ago" relative times in PMCC/IRA recent-activity sections) are unchanged — they're timezone-neutral by construction.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-tz-sweep-20260511-2145; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/templates/base.html.\$TAG \$BASE/trading_corp/web/templates/base.html; \
mv \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html.\$TAG \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html; \
mv \$BASE/trading_corp/web/templates/partials/polymarket_analysis.html.\$TAG \$BASE/trading_corp/web/templates/partials/polymarket_analysis.html; \
mv \$BASE/trading_corp/web/templates/research.html.\$TAG \$BASE/trading_corp/web/templates/research.html; \
TAG2=pre-tz-sweep-routes-20260511-2200; \
mv \$BASE/trading_corp/web/routes.py.\$TAG2 \$BASE/trading_corp/web/routes.py; \
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-11 19:30–20:30 UTC — Robinhood IRA dashboard reworks (PMCC-style rows + expert analysis parity)

**Triggered by:** Board feedback after the initial 19:00 UTC IRA dashboard ship. Three requested changes consolidated into this entry, three sequential deploys:

1. **PMCC-style click-to-expand rows** (replaced wide horizontal table). User said: "i want the open options UI to work just like robinhood pmcc."
2. **Section rename** — "Pure Assets" → "Portfolio", "Wheel Puts" → "Puts" with the wheel framing dropped entirely. User said: "there is no need for a wheel section. i do not run a wheel strategy per se."
3. **Expert Analysis parity** — analysis panel was deterministic but visually different from PMCC. User said: "you did not reuse the code built for robinhood pmcc."

**19:30 UTC deploy — PMCC-style rows + section rename (backup tag `pre-ira-pairs-20260511-1930`):**
- `trading_corp/web/data.py` — added `priority_score` / `priority_label` / `recommended_action` properties to `CoveredCallPosition` (mirrors `PMCCPair`'s priority model: urgent/elevated/routine/healthy + Roll/Close/Watch/Hold action). Renamed dict keys: `pure_assets` → `portfolio`, `wheel_puts` → `puts`. Sort order changed from "ITM first by DTE" to "priority_score desc, DTE asc as tiebreaker."
- `trading_corp/web/templates/partials/ira_pair.html` — **NEW**. Click-to-expand row mirroring `pmcc_pair.html`: priority dot + symbol + spot + "covered call" badge + recommended-action pill + DTE badge + Combined P&L on the right + chevron. Expanded body: LEFT panel = shares (qty / avg cost / last / mkt value / cost basis / P&L), RIGHT panel = short call (qty / delta / credit / mark / intrinsic / extrinsic / P&L). Visual parity with PMCC.
- `trading_corp/web/templates/partials/ira_dashboard.html` — rewritten: three sections renamed to **Covered Calls** (uses `ira_pair.html`) / **Portfolio** / **Puts** (hides entirely when no open puts; no wheel framing). List container renamed `id="pair-list"` so `static/js/pair_list.js` handles single-open accordion + "Loading {symbol}..." flash on the IRA rows too.

**20:00 UTC deploy — Expert Analysis stub renderer (backup tag `pre-ira-analysis-20260511-2000`):**
- Added htmx hookup to `ira_pair.html` summary (`hx-get="/division/{slug}/pair-analysis/{symbol}"` + target `#pair-analysis` + swap innerHTML). Added IRA dispatch in the existing `division_pair_analysis` endpoint (previously bailed for non-PMCC slugs at line 671). First version used a custom deterministic renderer `_render_ira_pair_analysis(cc)` showing breakeven / max profit / expiry scenarios.
- **Bug caught during verification:** `hx-sync="closest #ira-cc-list:replace"` was stale from before the list rename. In HTMX 2.x, an unresolvable `closest` selector prevents the request from firing entirely. Fixed to `closest #pair-list:replace` and redeployed.

**20:30 UTC deploy — PMCC renderer parity (backup tag `pre-ira-pmcc-renderer-20260511-2030`):**
- `trading_corp/web/routes.py`:
  - New `_analyze_ira_covered_call(cc, broker, deps)` async function. Returns `(PMCCAnalysis, TradeRecommendation | None)` — the SAME dataclass shapes PMCC produces — so `_render_pair_analysis` consumes IRA output without modification.
  - Rule-based action picker (no LLM call). Decision tree:
    - `0 DTE + ITM` → roll_short_early urgent (conf 0.95)
    - `0 DTE OTM` → hold routine (let expire)
    - `profit ≥85%` → close_short elevated
    - `≤2 DTE + ITM` → roll_short_early urgent (conf 0.90)
    - `≤2 DTE OTM` → hold routine (let theta finish)
    - `profit ≥70%` → close_short elevated
    - `ITM + >2 DTE` → watch elevated (with **preview-only** roll legs so user sees the trade shape even when not yet urgent)
    - otherwise → hold routine
  - Multi-paragraph rationale cites the specific rule (R1–R5) applied. Warnings cover assignment risk, credit-only roll requirement, partial coverage.
  - **Real chain fetch** via `broker.get_expiration_dates` + `broker.get_calls_for_expiry` for the "Sell to open" next-week leg. Picks the listed strike closest to `max(spot × 1.03, current_strike + 0.50)`. Returns `mark_per_share` / `bid` / `ask` / `delta` so spread-quality dots render. Falls back gracefully on chain-fetch failure (BTC leg only).
  - `_render_pair_analysis` gained `show_execute_button: bool = True` (default preserves PMCC behavior; IRA passes `False` to hide the Approve/Defer buttons since no IRA automation is wired — user executes manually in Robinhood).
  - IRA dispatch in `division_pair_analysis` calls the new analyzer, renders via `_render_pair_analysis(analysis, recommendation, slug, sym, show_execute_button=False)`, caches in `_pair_cache` (5-min TTL — same as PMCC).
  - The original custom `_render_ira_pair_analysis` is now dead code (kept for rollback safety, will be removed in a follow-up).

**Verification (post-20:30 UTC deploy, against real MARA position: 1200 shares avg $16.69, short 12× $13C 4DTE @ $0.92, spot $13.44):**
- Endpoint output: 3,471 bytes (vs. 1,071 bytes in the 20:00 stub).
- Markers confirmed: WATCH badge, 75% conf, Warnings, Rule R citations, Buy to close leg, Sell to open leg (real broker-fetched next-week $14C 11DTE @ $0.76), Net debit $252, Expected benefit ("Preview only — rules say WATCH"), MEDIUM cost confidence, no Approve/Defer buttons.
- Visual parity with PMCC confirmed in user screenshot — same urgency emoji + action badge + confidence + multi-paragraph rationale + warnings list + concrete trade legs + expected benefit structure.

**Inert / dormant:**
- Old `_render_ira_pair_analysis` function still in routes.py (marked deprecated). Remove on next cleanup pass.
- Rule tuning is in BACKLOG — current decision tree is the initial cut. Board flagged ≤2 DTE threshold for ITM-roll-urgency may want loosening to ≤4 DTE; deferred to a future tuning pass.

**Rollback recipes** (in reverse-deploy order; pick one):
```bash
# Rollback PMCC-renderer integration only (restores 20:00 stub renderer)
ssh azureuser@trading.jacksumner.com "
TAG=pre-ira-pmcc-renderer-20260511-2030; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/routes.py.\$TAG \$BASE/trading_corp/web/routes.py; \
sudo systemctl restart trading-corp.service
"

# Rollback to the original 19:00 IRA dashboard (wide table + Wheel Puts label)
ssh azureuser@trading.jacksumner.com "
TAG=pre-ira-pairs-20260511-1930; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/templates/partials/ira_dashboard.html.\$TAG \$BASE/trading_corp/web/templates/partials/ira_dashboard.html; \
rm -f \$BASE/trading_corp/web/templates/partials/ira_pair.html; \
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-11 20:17 UTC — Polymarket Copy Trader division (paper-mode live)

**Commits:** none — files patched onto prod's drifted content (per `trading_corp_prod_git_drift` memory). Parallel BitUnix sprint was running on the same VM; patcher applied K3-equivalent additions on top of prod's current state, not git HEAD.
**Triggered by:** User re-prioritized `polymarket_copy_trading` from deprioritized → active build the same day K3 shipped. Goal: validate the copy-trading thesis on a SECOND venue in parallel, leveraging Polymarket's free public Data API (no Apify-equivalent cost), explicit side+outcome in `/activity` (no trade-tape inference), and the venue-agnostic scoring engine already built for K3.
**Backup tags:**
- `pre-polymarket-copy-20260511-2011` — covers `kalshi_whale_stats.py`, `persistence/db.py`, `polymarket_resolver.py`, `main.py`, `config/strategies.yaml` (5 modified)
- `pre-pm-enable-20260511-2017` — strategies.yaml backup before flipping `enabled: true`

**Files deployed (5 new, 5 modified):**
- `trading_corp/data/polymarket_data_api_client.py` — **NEW** (~340 lines). Async wrapper over Polymarket's public REST endpoints at `data-api.polymarket.com`. Dataclasses: `LeaderboardEntry`, `ActivityRow`, `PositionRow`. Endpoints: `/v1/leaderboard?category=<C>&limit=N&offset=N` (discovery, supports 5 working categories — Politics/Sports/Crypto/Tech/Mentions), `/activity?user=<wallet>&limit=N` (per-wallet trade history with explicit side/outcome/price/USDC size), `/positions?user=<wallet>` (current open). Plus `fetch_market_resolutions(condition_ids)` hitting `gamma-api.polymarket.com/markets` in BOTH open + closed variants per chunk (gamma-api defaults to `closed=false` and intersects with `condition_ids` filter — needs two passes to capture both states). `_decode_resolution` distinguishes resolved (one price ≥0.9 → win_idx), void (closed but all-near-zero prices), pending (closed=false). All free, no auth.
- `trading_corp/data/polymarket_whale_stats.py` — **NEW** (~225 lines). Venue-specific stats adapter. `compute_polymarket_stats(leaderboard_entry, activity_rows, market_resolutions, half_life_days)` builds a `WhaleStats` record by filtering BUY trades through resolution lookup, computing time-weighted Wilson-LCB + ROI from real entry-price + USDC-size math. `_is_win_for_buy` joins activity outcome_index against winning_outcome_index. Reuses venue-agnostic `wilson_lcb_95`, `_edge_factor`, `_category_bonus` from `kalshi_whale_stats`.
- `trading_corp/agents/strategies/polymarket_copy_trader.py` — **NEW** (~370 lines). Strategy. Per-cycle: load selected whales, fetch `/activity` per whale, filter to TRADE rows newer than `last_seen_ts` + dedup by `transaction_hash`. BUYs emit copy ProposedOrders (sized via USDC bet-size tiers $1/$2/$5), SELLs of held positions emit close orders. **`qty` in CONTRACTS** (`copy_usdc / entry_price`) so the resolver's `notional = qty * price` math is consistent. `limit_price` = whale's entry price. Side detection explicit (no Kalshi-style size-match). Cold-start safe.
- `trading_corp/scripts/refresh_polymarket_whales.py` — **NEW** (~310 lines). Quarterly selection orchestrator. Rule B: top-2 per cat × 5 cats + top-2 global = 12. Pulls leaderboard per cat + global → enriches via `/activity?limit=200` → batch-fetches market resolutions (gamma-api, 50-id chunks, open+closed variants) → scores per (whale, target_category) → picks rule B. Cost: $0. Time: ~5s for 100+ candidates.
- `tests/test_polymarket_copy_trader.py` — **NEW** (~340 lines, 23 tests). All pass; full suite 387 tests, zero regressions.
- `trading_corp/data/kalshi_whale_stats.py` — extended with `wilson_lcb_95_weighted(weighted_wins, n_eff)` (Kish's effective sample size) and `time_weighted_outcomes(samples, now_ts, half_life_days)` (exp decay, default 30d half-life). Venue-agnostic.
- `trading_corp/persistence/db.py` — added `division TEXT NOT NULL DEFAULT 'polymarket_arbitrage'` column to `polymarket_round_trips` (was implicitly arbitrage-only). New `_maybe_add_column()` helper for idempotent `ALTER TABLE ADD COLUMN` migrations. `init_db` calls it, then creates `ix_polymarket_round_trips_division` index AFTER the migration (intentionally NOT in SCHEMA to avoid CREATE-INDEX-on-missing-column on upgraded DBs). Verified on a pre-migration prod-shaped DB.
- `trading_corp/agents/polymarket_resolver.py` — `_fetch_unresolved_orders` widened from `actor = 'polymarket_arbitrage'` to `actor IN ('polymarket_arbitrage', 'polymarket_copy_trader')` + carries `_actor` field. `_compute_round_trip_row` stamps `division` from payload, falling back to actor-name inference (`polymarket_copy_trader` → `polymarket_copy_trading`). Slug/title fallbacks for copy-trader payload shape.
- `trading_corp/main.py` — `_scheduled_polymarket_copy_trader_loop(agent, *, channel, logger_agent, data_exec, risk_agent, db_url)` mirrors `_scheduled_kalshi_copy_trader_loop` shape but takes NO Apify token + NO trade-tape-fetcher. Owns the `PolymarketDataAPIClient` lifecycle. Audit base_payload enumerates 17 K3-equivalent Polymarket fields. Telegram emoji 🟣 (distinguishes from K3's 🐋). Startup wiring sits right after `polymarket_arb_task`.
- `config/strategies.yaml` — `polymarket_copy_trader:` block appended. Default `enabled: false` → flipped to `true` via in-place sed after first successful restart.

**Features shipped:**
- New division: `polymarket_copy_trading` flipped from standby-placeholder to active. Same wallet as polymarket_arbitrage shared during paper-mode per CLAUDE.md (separate wallet planned for live-mode per Jack).
- 12 selected whales committed to `agent_state(polymarket_copy_trader.selected_whales)`. All opt-in public, no anonymity gradient (vs Kalshi K3's ~7%). Top whale `248188374`: 197 resolved, 100% WR, $133K lifetime P&L, Sports specialist.
- Polymarket Data API wired as first-class data source — discovery + per-wallet enrichment + resolution batch all free, no auth, no Apify-equivalent recurring cost.
- Time-weighted Wilson LCB in the venue-agnostic scoring engine — Kalshi K3 could opt in too via `half_life_days` param.
- `polymarket_round_trips.division` column lets the existing resolver pipe BOTH arbitrage and copy_trading round-trips into the same table.

**Notable code decisions:**
- **Recon agent's `/leaderboards` endpoint was hallucinated.** Real endpoint is `/v1/leaderboard` (singular, with `/v1/` prefix). Documented URL returns 404. Don't trust agent-cited URLs without a fresh probe.
- **5 working categories, not 12.** Polymarket's taxonomy has 9 top-level but only Politics/Sports/Crypto/Tech/Mentions return leaderboard data. Rule B adjusted from "top-1 per cat × 12 volume cats" to "top-2 per cat × 5 + top-2 global = 12".
- **gamma-api's `condition_ids` filter intersects with `closed=false` by default.** Required two passes per chunk (open variant + closed variant) to capture both market states.
- **`qty` in CONTRACTS, not USDC.** Originally emitted in USDC, but the resolver's binary-settlement math requires contracts. Normalization: `contracts = copy_usdc / entry_price`.
- **Multi-leg sports markets won't auto-resolve in v1.** Resolver's `_compute_round_trip_row` gates on `outcome.lower() in {"yes", "no"}`. Spurs/Cavaliers/etc. land in audit_event but not polymarket_round_trips. Acceptable v1 gap; resolver extension is small follow-up.
- **No trade-tape inference needed.** Polymarket's `/activity` carries `side: BUY|SELL` + `outcome_index: 0|1` + human `outcome` label directly. K3's size-match dance is venue-specific to Kalshi.

**Verification:**
- Pre-restart import smoke on prod: all 8 Polymarket modules + `trading_corp.main` import cleanly under prod's venv.
- PID rotation 260521 → 261879 on first restart, → 262635 on a parallel-session restart 2 min later (BitUnix sprint concurrent; no file collisions).
- Schema migration verified: `division` column present with DEFAULT `'polymarket_arbitrage'`. Existing rows backfilled.
- "Polymarket copy trader scanner online (enabled=False)" at 20:13:01, then "(enabled=True)" after the strategies.yaml flip at 20:17.
- **Cold-start fired at 20:16:24-27 UTC** — 12 `polymarket_copy_cold_start` audit events. 11 whales got populated baselines (15-20 rows each); 1 (`Talvez10`) returned empty (will baseline on next cycle).
- 23 new unit tests pass; full suite 387 tests, zero regressions.

**First selection (committed to prod 2026-05-11 20:14 UTC):**
- Sports×2: `248188374` (197 resolved, 100% WR), `ic4cream` (99 resolved, 93% WR)
- Tech×2: `OnlySafeBets` (107 resolved, 83% WR), `wenzhu` (53 resolved, 79% WR)
- Crypto×2: `ddssaaas6` (166 resolved, 89% WR), `0xE9Ba96828e513a...` (191 resolved, 77% WR)
- Politics×2: `VladimirPooper` (130 resolved, 94% WR), `mohahaha` (17 resolved, 88% WR)
- Mentions×2: `Pedrobeliever47` (11 resolved, 82% WR), `0xe617861a96631d...` (71 resolved, 94% WR)
- GLOBAL×2: `00xx00xx00` (112 resolved, 58% WR but +$1.08/$ ROI), `Talvez10` (180 resolved, 67% WR)

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-copy-20260511-2011; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/data/kalshi_whale_stats.py.\$TAG       \$BASE/trading_corp/data/kalshi_whale_stats.py; \
mv \$BASE/trading_corp/persistence/db.py.\$TAG                \$BASE/trading_corp/persistence/db.py; \
mv \$BASE/trading_corp/agents/polymarket_resolver.py.\$TAG    \$BASE/trading_corp/agents/polymarket_resolver.py; \
mv \$BASE/trading_corp/main.py.\$TAG                          \$BASE/trading_corp/main.py; \
mv \$BASE/config/strategies.yaml.\$TAG                        \$BASE/config/strategies.yaml; \
rm -f \$BASE/trading_corp/data/polymarket_data_api_client.py \
      \$BASE/trading_corp/data/polymarket_whale_stats.py \
      \$BASE/trading_corp/agents/strategies/polymarket_copy_trader.py \
      \$BASE/trading_corp/scripts/refresh_polymarket_whales.py \
      \$BASE/tests/test_polymarket_copy_trader.py; \
sudo systemctl restart trading-corp
"
```

(The `division` column on `polymarket_round_trips` survives the rollback — additive schema, no rollback needed. `agent_state.selected_whales` persists; harmless without the strategy code.)

---

## 2026-05-11 19:00 UTC — Robinhood IRA detailed dashboard (covered calls + pure assets + wheel puts)

**Triggered by:** Board direction — IRA strategy is buy-and-hold + sell weekly covered calls (no LEAPs allowed in retirement accounts; shares must back the short calls). Occasional cash-secured puts as a wheel entry. The existing `/division/robinhood_ira` page used the generic PMCC/Holdings layout which doesn't model this — covered calls weren't grouped with their underlying shares, and the page showed an empty "Positions" section because there are no PMCC pairs in IRA.

**Files deployed (1 new, 2 modified, backup tag `pre-ira-dashboard-20260511-1900`):**
- `trading_corp/web/templates/partials/ira_dashboard.html` — **NEW**. Three sections:
  - **Covered Calls** — shares + short call grouped by underlying; one row per (underlying, short_call); columns: Symbol / Shares / Cost / Last / Mkt Value / Share P&L | Call (DTE / Strike / Credit / Mark / Call P&L / Status). Coverage% badge (e.g. "fully covered" or "75% covered" if partial). ITM strikes flagged red with breach %. Sort: ITM-first, then by DTE ascending.
  - **Pure Assets** — shares without any short call sold against them; columns: Symbol / Qty / Avg Cost / Last / Mkt Value / Unrealized P&L. Suppresses P&L for rows with cost_basis=0 (avoids the RH crypto cost_basis=0 noise — same rule as `feedback_holdings_window_scope` memory). Sort: market value descending.
  - **Wheel Puts** — short cash-secured puts (acquire-on-assignment); columns: Underlying / Strike / DTE / Qty / Credit Received / Mark / Underlying Px / Net Basis if Assigned / P&L. Renders empty-state when no active puts ("Sell puts to enter on dips and collect premium.").
- `trading_corp/web/data.py` — added 2 dataclasses + 1 builder:
  - **`CoveredCallPosition`** — `underlying`, `shares_qty`, `shares_avg_price`, `shares_market_value`, `shares_cost_basis`, `shares_pnl`, `shares_pnl_pct`, `short_call: OptionLeg`, `coverage_pct`. Properties: `is_fully_covered`, `is_itm`, `breach_pct`, `combined_pnl`, `call_status` (itm / expiring_today / expiring_tomorrow / profit_take_candidate / open).
  - **`WheelPutPosition`** — wraps a short-put `OptionLeg`. Properties: `underlying`, `strike`, `expiry`, `days_to_expiry`, `credit_received`, `cost_to_close`, `is_itm`, `assignment_cost`, `effective_basis_if_assigned`.
  - **`build_ira_view(stock_holdings, legs, prices) -> dict`** — partitions option legs by type/side, groups short calls with their underlying shares, identifies pure assets (shares with no matching call), wraps short puts as wheel positions. Returns `{covered_calls, pure_assets, wheel_puts}`.
  - New `ira_view: dict | None` field on `DivisionViewSnapshot`. Wired in `build_division_view` for `slug == 'robinhood_ira'`.
- `trading_corp/web/templates/division.html` — conditional fork: when `slug == 'robinhood_ira' AND view.ira_view`, include `partials/ira_dashboard.html` and skip the generic PMCC pairs / Holdings tables. Falls back to legacy layout for all other slugs.

**Verification (against real prod IRA data immediately post-deploy):**
- Real IRA holdings: 7 stocks (IBIT 118.04, MARA 1200, BLOX 18.74, GME+ 100, MSTY 200, STRC 0.97, SATA 0.51) + 1 short call (MARA 2026-05-15 $12.50 ×12, credit $0.92/sh).
- Grouping result: **1 covered call (MARA, 100% covered, OTM, combined P&L -$4,404)** + **6 pure assets** (sorted by market value desc) + **0 wheel puts** (empty-state rendered).
- Rendered page: section headers "Covered Calls" / "Pure Assets" / "Wheel Puts" all present; legacy "Positions" / "Holdings" suppressed for IRA slug; "Recent activity" preserved.
- Specific data points confirmed in HTML: "MARA", "1200", "2026-05-15", "×12", "fully covered" badge.

**Inert / dormant:**
- Long-call legs (LEAPs) on the IRA broker filter are silently dropped in `build_ira_view` — they shouldn't exist there per the strategy, but defensive.
- No automated trading wired — this is dashboard-only. Strategy automation is a follow-up.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-ira-dashboard-20260511-1900; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG  \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG  \$BASE/trading_corp/web/templates/division.html; \
rm -f \$BASE/trading_corp/web/templates/partials/ira_dashboard.html; \
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-11 18:23 UTC — BitUnix Phase 3.2.3 — live confluence score dashboard panel

**Triggered by:** Phase 3.2 (score accumulator) and 3.2.2 (PA factors) were live but invisible — to understand what the bot was scoring, you had to grep audit_event. Phase 3.2.3 adds a panel to `/division/bitunix_futures` that surfaces it.

**Files deployed (1 new, 2 modified, backup tag `pre-bitunix-323-20260511-1820`):**
- `trading_corp/web/templates/partials/bitunix_score_panel.html` — **NEW**. Tailwind+htmx panel. Auto-refreshes every 30s via `hx-get` self-referential pattern.
- `trading_corp/web/data.py` — added `build_bitunix_score_view(db_url, deps)` builder + `_parse_audit_ts(ts)` helper. New `bitunix_score: dict | None` field on `DivisionViewSnapshot`. Wired conditionally in `build_division_view` for `slug == 'bitunix_futures'`.
- `trading_corp/web/templates/division.html` — added conditional include block (5 lines) mirroring the donchian pattern.

**Panel surfaces:**
- **Header:** scoring enabled/dormant, factor count (34), tier thresholds, fire threshold (8)
- **4 stat cards:** Last eval (tier + signal + age), Net score (with buy/sell breakdown + guard penalties), Cooldown (per-side remaining time), Bar cache health (bars cached + last close + ATR + refresh errors)
- **Live price-action factors strip:** ✓/○ per PA factor (`above_vwap`, `below_vwap`, `HH_4h`, `LL_4h`, `volume_above_avg`) + pct_change(60m) — computed live from `bar_cache` at request time via `compute_price_context()`
- **Buy/Sell contributions side-by-side** for the latest evaluation, listing every contributing signal name with its weight
- **Recent paper fires table** (last 10 `would_have_placed` rows with `via=bitunix_score`): ts / tier / side / net_score / entry / stop / TP / qty / trigger
- **Recent evaluations table** (last 20 `bitunix_score_decided` rows): with tier color coding, outcome (placed / skipped_cooldown / skipped_score)
- **Ledger window summary:** count of rows in last 24h

**Verification (in prod immediately post-deploy):**
- `curl localhost:8000/division/bitunix_futures` returned 36,633 bytes ✓
- `id="bitunix-score-panel"` present in HTML ✓
- "● SCORING ACTIVE" badge rendered (scoring.enabled=True) ✓
- Live PA factors strip showed real bool flags: above_vwap=✓, HH_4h=✓, LL_4h=✓ (outside-bar case captured visually) ✓
- Tier mentions count: 1 PREMIUM (threshold label) + 2 STANDARD (1 label + 1 history row) + 1 SKIP (last eval status) ✓
- "Recent paper fires (1)" rendered (the 18:00:07 STANDARD SELL) ✓
- "Recent evaluations (7) · ledger 24h: 7 rows" rendered ✓

**Notable design:**
- Auto-refresh via `hx-get="/division/bitunix_futures" hx-trigger="every 30s" hx-select="#bitunix-score-panel" hx-target="#bitunix-score-panel"` — re-fetches the whole division page but only swaps the panel subtree. No new endpoint needed.
- `build_bitunix_score_view` returns `None` when scoring config is unavailable (observer not wired or YAML scoring block missing) → template's `{% if view.bitunix_score %}` gate prevents partial rendering. Safe default.
- Guard penalties (`bg`, `sg`) and `cooldown_blocked` flag both surfaced — explains "why didn't fire" without grepping logs.
- 30s refresh is intentional. Bar cache polls every 60s; webhooks arrive a few times per hour during active periods. 30s is the sweet spot for "looks live" without hammering the SQLite reads.

**Inert / dormant:** none. The panel is read-only telemetry; it does not affect order flow.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-323-20260511-1820; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG  \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG  \$BASE/trading_corp/web/templates/division.html; \
rm -f \$BASE/trading_corp/web/templates/partials/bitunix_score_panel.html; \
sudo systemctl restart trading-corp.service
"
```

---

## 2026-05-11 18:17 UTC — Kalshi K3 Copy Trading division (paper-mode live)

**Commits:** none — files patched onto prod's already-drifted content (per `trading_corp_prod_git_drift` memory).
**Triggered by:** K3 sprint per BACKLOG.md "P0 NEXT — Kalshi K3 Copy Trading". Mirror top Kalshi whales' positions at scaled-down size; selected whales come from offline Wilson-LCB × ROI × category scoring; side detection uses Kalshi's free public trade tape.
**Backup tags:**
- `pre-kalshi-k3-20260511-1816` — covers `kalshi.py`, `secrets.py`, `main.py` (3 modified)
- `pre-kalshi-k3-enable-20260511-1819` — covers `strategies.yaml` (enabled-flip backup)

**Files deployed (5 new, 3 modified):**
- `trading_corp/data/kalshi_apify_client.py` — **NEW** (~260 lines). Async wrapper over Apify's two saswave Kalshi actors (`leaderboard-scraper` + `profile-scraper`). Typed dataclasses (LeaderboardEntry, WhaleProfile, WhalePosition, WhaleTrade), structured error class hierarchy (Auth / OverCap / Timeout), semaphore-gated concurrency, stub-safe when token missing.
- `trading_corp/data/kalshi_whale_stats.py` — **NEW** (~210 lines). Venue-agnostic scoring engine. Wilson 95% LCB on win rate (penalizes small samples), edge factor from avg pnl-per-contract (clipped), category specialization bonus (1.5x match). `compute_stats` aggregates closed_positions per nickname; `score_whale` produces composite + exclusion reasons. Same math will plug into Polymarket revival.
- `trading_corp/agents/strategies/kalshi_copy_trader.py` — **NEW** (~360 lines). The Phase K3 strategy. Mirrors `kalshi_llm_arbitrage` shape (mtime-cached config reload, `enabled` / `auto_execute` properties, `run_scan_cycle`). Per-cycle: load selected whales from `agent_state`, fetch their open_positions via Apify, compare to last-known snapshot, emit ProposedOrders for entries (with side detection) and exits. Cold-start safe: first poll per whale records baseline + emits nothing. Side detection conservative: low-confidence → skip entry, never copy wrong side.
- `trading_corp/scripts/refresh_kalshi_whales.py` — **NEW** (~280 lines). One-off CLI orchestrator for quarterly selection refresh. Pulls leaderboards per category, enriches top-N candidates with profile + closed_positions, scores via Wilson LCB × ROI × category match, writes top whales to `agent_state(kalshi_copy_trader.selected_whales)`. `--dry-run`, `--min-composite` quality floor, fill-up from leftover pool when per-category dedup leaves slots open.
- `trading_corp/scripts/__init__.py` — **NEW** (empty, package marker).
- `trading_corp/brokers/kalshi.py` — extended with `KalshiPublicTrade` dataclass + `get_market_trades(ticker, since, until, limit)` method wrapping `pykalshi.AsyncMarket.get_trades`. Free Kalshi public API, anonymous at trader level, returns `taker_side` per trade — the side-detection signal. Strategy depends on a `TradeTapeFetcher` Protocol; `KalshiBroker` structurally satisfies it.
- `trading_corp/utils/secrets.py` — `APIFY_API_TOKEN` plumbed (5 edits: redact tuple, `Secrets` dataclass field, `expected_env_vars`, `load_secrets()` init, redact-literal registration). Stub-safe — strategy no-ops if token missing.
- `trading_corp/main.py` — `_scheduled_kalshi_copy_trader_loop` function (~155 lines) + startup wiring after the `kalshi_llm_task` block. Apify client lifecycle owned by the loop (`async with KalshiApifyClient(...) as apify_client`). Audit payload allowlist enumerates 10 K3-specific fields (per `trading_corp_audit_payload_allowlist` gotcha memory): `ticker`, `outcome`, `is_entry`, `whale_handle`, `whale_position_contracts`, `whale_position_pnl`, `copy_size_usd`, `side_detection_confidence`, `first_seen_iso`, plus standard.
- `config/strategies.yaml` — `kalshi_copy_trader:` block. Already on prod from a parallel session push at md5 d2619e32; flipped `enabled: false → true` per Board direction (paper-mode, so safe).

**Features shipped (load-bearing for future "is X done?" checks):**
- New division: `kalshi_copy_trading` flipped from standby-placeholder to active (strategy live; paper-mode auto-execute on the existing PaperBroker).
- Selected whales committed to `agent_state(kalshi_copy_trader.selected_whales)`: `['smedtoshi', 'NovaRex', 'tom14cat14', '9187234']`.
- Apify Starter ($29/mo Bronze) subscription confirmed live; APIFY-API-TOKEN in KV `kv-tc-vtwbowt3wtkpy`; loaded at startup via managed identity.
- Two-stage discovery+scoring pipeline ships as the standalone `refresh_kalshi_whales` script — re-runnable quarterly.
- `KalshiBroker.get_market_trades` is the new public side-detection signal source. Free, anonymous-at-trader-level. Will be reused by future Kalshi strategies that need short-window trade context.

**Notable code decisions:**
- **`max_results` is ignored by saswave's profile actor.** Empirically: `open_positions` returns a 20-row floor per name; `trades` returns a 50-row floor. Cost-model planned around this — opaque whales return 0 rows (free), visible whales return up to 20.
- **Two-tier polling architecture was DEFERRED.** Original plan used profile-watch (cheap) + on-activity position fetch (expensive). Once we upgraded to Bronze, simple polling at 5min on 4 whales (~$120/mo budget) is cleaner and survives whale-activity bursts. Two-tier code path doesn't exist; could be added back via the `WhaleActivitySource` abstraction if 12-whale config blows the budget.
- **Side detection is conservative.** When the Kalshi public trade tape can't disambiguate a whale's entry (no size-match or ambiguous matches), the strategy SKIPS the entry rather than guessing. Better to miss a copy than copy the wrong side on real money later.
- **Cold-start baseline persists with `our_side=""`.** When a whale closes one of those baselined positions, `_emit_exit` correctly short-circuits because there's no `our_side` stored — no phantom close emitted.
- **Strategy `enabled` and `auto_execute` are independent flags.** `enabled: true` runs the scanner + emits ProposedOrders + logs `would_have_placed` to audit (paper-mode). `auto_execute: true` would route approved orders through a real KalshiLiveBroker (Phase K5+ work; doesn't exist yet).

**Bugs caught + fixed during the session:**
- `set_agent_state` / `load_agent_state` argument order. The actual signature is `(agent, key, value, db_url=...)` but I wrote `(db_url, agent, key, value)` positionally in both the strategy and the script. First selection-script commit attempt failed with `'list' object has no attribute 'startswith'` because the db_url positional slot got a list. Fixed in both files before deploy.
- Selection fill-up logic was capping at 3 picks even when 9 viable whales existed. Per-category top-2 was deduping aggressively across categories with the same dominant whales. Fix: after per-category dedup, fill remaining slots from leftover-viable global pool by composite score.
- No quality floor on composite score. Without one, fill-up was including whales with Wilson LCB ≈ 0 and negative edge (some 0% win-rate whales were in the top 9). Added `--min-composite` CLI flag (default 0.30) — filters Wilson-LCB-zero whales out of selection. Final selection: 4 quality whales instead of 9 mediocre ones.

**Visibility finding (the data, not a bug):**
- Kalshi has a strong **privacy gradient**. Top-of-leaderboard whales (by `volume`, `projected_pnl`, or `num_markets_traded`) are systematically opaque — 0 of 14 candidates exposed `closed_positions` on the first `--candidates 5` run. Going to `--candidates 30` surfaced 9 visible whales out of 123 candidates (~7% visibility rate). Mid-tier traders (leaderboard rank 20-100) are the actual addressable pool for copy trading.
- All 4 selected whales are Sports/Crypto specialists. No Politics/Economics/Climate/Financials specialists made the visibility-and-quality-floor cut in this first selection pass.

**Cost projection (Bronze rates):**
- Apify Starter base: $29/mo (includes $29 prepaid usage)
- Polling: 4 whales × 20-row floor × $0.0015 × 288 polls/day × 30 = ~$83/mo
- Quarterly selection refresh: ~$0.30 per run = ~$0.10/mo amortized
- Expected total: **$30-50/mo** (well under the $300 spending limit Jack should set in Apify dashboard)
- This session's burn: ~$1.50 (verified via two-test exploration + one final commit run)

**Verification:**
- PID rotation: 246347 → 249182.
- Service active, web `/healthz` returns HTTP 200 in 150ms.
- Pre-restart Python import smoke succeeded on all 7 K3 modules + `trading_corp.main` under prod's venv.
- "Kalshi copy trader scanner online (enabled=False, auto_execute=False, hitl=DIRECT)" logged at 18:17:50 UTC.
- `enabled: true` flipped via sed-anchored replacement at 18:19 UTC. Verified no other strategies accidentally toggled (`grep -B 1 "enabled: true"` showed only pre-existing enabled strategies + ours).
- **Cold-start fired cleanly at 18:22:56 UTC** (first scheduled poll, 300s after restart). Per-whale baselines: smedtoshi=0, NovaRex=0, tom14cat14=14, 9187234=20 open positions. 4 `kalshi_copy_cold_start` audit rows inserted. Zero ProposedOrders emitted (cold-start protection working as designed).
- 32 K3-specific unit tests pass; full suite (excluding 3 pre-existing-broken test files unrelated to K3) shows 364 tests passing — zero regressions from K3 work.

**Inert / dormant on current traffic:**
- smedtoshi and NovaRex are currently flat (0 open positions). They'll trigger entries only when they next open a Kalshi position — could be hours or days. tom14cat14 (14 open) and 9187234 (20 open) baselined positions won't trigger phantom exits because `our_side=""` on baseline.
- Exit-emission code path is wired but won't fire until we successfully emit at least one ENTRY (which requires side-detection to succeed for that ticker). Until that happens, the strategy is effectively read-only on prod.
- `--metric` defaults to `num_markets_traded` in the refresh script. Future refresh attempts could try `--metric volume` or `--time monthly` to surface different whales. Quarterly refresh is the planned cadence.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k3-20260511-1816; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/brokers/kalshi.py.\$TAG       \$BASE/trading_corp/brokers/kalshi.py; \
mv \$BASE/trading_corp/utils/secrets.py.\$TAG        \$BASE/trading_corp/utils/secrets.py; \
mv \$BASE/trading_corp/main.py.\$TAG                 \$BASE/trading_corp/main.py; \
rm -f \$BASE/trading_corp/data/kalshi_apify_client.py \
      \$BASE/trading_corp/data/kalshi_whale_stats.py \
      \$BASE/trading_corp/agents/strategies/kalshi_copy_trader.py \
      \$BASE/trading_corp/scripts/refresh_kalshi_whales.py \
      \$BASE/trading_corp/scripts/__init__.py; \
rmdir \$BASE/trading_corp/scripts 2>/dev/null; \
ENABLETAG=pre-kalshi-k3-enable-20260511-1819; \
mv \$BASE/config/strategies.yaml.\$ENABLETAG  \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp
"
```

(The selected_whales entry in `agent_state` is left in place by the rollback — harmless data with no code to consume it.)

---

## 2026-05-11 18:03 UTC — BitUnix Phase 3.2.2 — price-action factors wired into score path

**Triggered by:** Phase 3.2.1 (deployed 17:52 UTC) ran with a zero-filled `PriceContext` — the 5 price-action factors (`above_session_vwap`, `below_session_vwap`, `higher_highs_4h`, `lower_lows_4h`, `volume_above_20bar_avg`) and the two guard penalties (`sell_on_rush`, `buy_on_fall`) were defined in YAML but inert in live mode. Phase 3.2.2 wires them.

**Observation between deploys:** Phase 3.2.1's first STANDARD SELL fired at **18:00:07 UTC** (≈8 min after the 17:52 deploy), net_score=11 (sell-side accumulation of `mc_b_sell_circle` + `mc_a_red_diamond` + `mc_b_sell_circle_div`). The multi-bar accumulation design fired as intended on the first real opportunity post-deploy. Paper short opened at $81902.5, qty=0.0038 BTC.

**Files deployed (1 new, 2 modified, 1 backup tag `pre-bitunix-322-20260511-1810`):**
- `trading_corp/data/bitunix_price_context.py` — **NEW**. Pure helpers: `session_vwap()`, `higher_highs_lower_lows_4h()`, `volume_above_20bar_avg()`, `pct_change_in_window()`, `_resample_to_4h()`. Aggregator `compute_price_context(bar_cache, sell_window_min, buy_window_min)` returns a `PriceContext` or None (None → caller falls back to zero context).
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — `_score_and_maybe_propose()` now calls `compute_price_context(self.bar_cache, ...)` instead of building a zero-filled PriceContext. Graceful fallback on any exception (logs warning, uses zero context).
- `trading_corp/main.py` — bumped `LiveBarCache(max_bars=60)` → `max_bars=500`. **Surgical edit** (3-line replacement around the constructor). BitUnix API actually caps at 200 bars per request, so live cache settles at 200 bars regardless — but the YAML still requests 500 for forward-compat (if the venue limit ever raises).

**Features shipped:**
- Live VWAP comparison: each score includes ±1 weight from `above_session_vwap` / `below_session_vwap` based on current price vs day-VWAP (or rolling-10h VWAP at runtime when cache doesn't span the full UTC day).
- 4h HH/LL: each score includes ±2 weight from comparing last-completed 4h bucket vs prior. Resampling done in-memory at evaluation time from the 3m bars.
- Volume-above-avg: ±1 (directional — adds to both sides as a strength-of-move indicator).
- Guard penalties: `sell_on_rush` / `buy_on_fall` now compute actual % change over the 60-min window from cached bars. Tiered penalties (-1 / -2 / -3) suppress sells into rapid rises and buys into rapid drops.

**Notable code changes:**
- `compute_price_context` is the only public API. Internal helpers (`session_vwap`, etc.) are also exported for unit testing.
- `_resample_to_4h` aligns 4h buckets to UTC 00:00 / 04:00 / 08:00 / 12:00 / 16:00 / 20:00 — matches the convention in `backtest_btc_accumulator._resample_to_4h`.
- HH/LL check requires **≥ 3 buckets** (last bucket is in-progress, excluded). At max_bars=200, that's ~10h of 3m bars = 2.5 buckets, JUST enough.

**Verification:**
- Local synthetic-bar test passed: 500 bars dropping 82000→81002 produced `below_vwap=True`, `LL_4h=True`, `HH_4h=False`, `volume_above_avg=True`, `pct_change=-0.049%`.
- Prod import test ✓
- /healthz=200 after warm-up ✓
- Bar cache primed: 200 bars cached, last_close=$81890.2, atr_14=98.43, poll-loop online (60s interval) ✓
- Pending: first post-deploy webhook to land a score row with non-zero PA contributions (cooldown blocks sell-side until 18:30:07 from the STANDARD fire at 18:00:07).

**Inert / dormant on current traffic:**
- `bitunix_futures.scoring.tier_thresholds.weak: 5` band — still never fires (`min_score_to_fire: 8`).
- Phase 3.1 `_tier_for` classifier + `_maybe_propose` — still retained for fast rollback.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-322-20260511-1810; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG  \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG  \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
rm -f \$BASE/trading_corp/data/bitunix_price_context.py; \
sudo systemctl restart trading-corp.service
"
```
(Phase 3.2.1 state restored — score path still active, PA factors inert again.)

---

## 2026-05-11 17:52 UTC — BitUnix Phase 3.2 confluence score accumulator (paper-mode, multi-deploy)

**Triggered by:** Board ask after the 16:42 UTC missed-short incident — the Phase 3.1 single-bar `_tier_for` classifier dropped a clean PREMIUM SELL setup (4h-bear bias + multiple 4h/1D bear Cypher signals accumulated + simultaneous `money_bag_top` + `cvd_bear_flip`) because CVD agreement check fired at trigger time before the same-second `cvd_bear_flip` updated state. Root cause was structural: classifier evaluates one snapshot at one moment, can't accumulate confluence across bars.

**Replacement design (Phase 3.2):** Score accumulator. Every inbound webhook signal (Otter + Cypher) appends to `bitunix_signal_ledger` with a per-factor TTL. On each new alert, scorer sums weights of all live (in-TTL, deduped by signal_name) signals + price-action factors, applies guard penalties, picks the winning side, maps net_score → PREMIUM (≥12) / STANDARD (≥8) / WEAK (≥5) / SKIP. Cooldown (1800s) prevents stacking same-direction fires. Risk caps unchanged (0.5% per-trade effective risk, 3% daily kill).

**Backtest verdict** (Apr 30 – May 9, 625 alerts, tuned config):
- 21 paper trades, 42.9% win rate, **+0.286 R avg, +6.0 R total, +0.18% return, 0.25% max DD**
- STANDARD tier carries edge (+0.33 R, 44%, n=18); WEAK band killed via `min_score_to_fire: 8` (was -0.16 R noise)
- 16:42 setup fires as PREMIUM SELL (net_score=12) on the new model — validated standalone before deploy
- Context: BTC was up 5.79% in window (bull); model navigated bullish chop reasonably

**Files deployed (4 new/modified, 2 backup tags):**
- `config/strategies.yaml` — added `bitunix_futures.scoring` block (34 factors, tier thresholds, guards, dedupe). `enabled: true` at ship.
- `trading_corp/agents/strategies/bitunix_confluence.py` — **NEW**. Pure-function scorer; reuses `FactorConfig`/`GuardConfig`/`AlertEvent`/`PriceContext` from `btc_accumulator.py`. Adds `BitUnixConfluenceConfig`, `evaluate_confluence_futures()`, `filter_live_alerts_with_dedupe()`.
- `trading_corp/agents/strategies/btc_accumulator.py` — **NEW on prod** (existed locally as scaffold for the deprecated coinbase_spot accumulator; needed because `bitunix_confluence.py` imports its dataclasses). Pure-function, no side effects.
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — extended. Adds 3 new DDLs (`bitunix_signal_ledger`, `bitunix_score_cooldown` + index). `__init__` accepts optional `scoring_config: BitUnixConfluenceConfig`. `observe_and_decide()` now: (a) always appends to ledger regardless of flag, (b) routes to `_score_and_maybe_propose()` when `scoring_config.enabled=True`, else falls back to Phase 3.1 `_maybe_propose()`. New methods: `_append_to_ledger`, `_read_live_ledger`, `_read_cooldown`, `_record_score_fire`, `_log_score_decision`, `_score_and_maybe_propose`. New audit kind: `bitunix_score_decided` (separate from Phase 3.1's `bitunix_decided`). Score-path fills also tag `would_have_placed` with `via: "bitunix_score"` + `net_score` for filtering.
- `trading_corp/main.py` — loads `BitUnixConfluenceConfig` from `strategies.yaml`, passes to observer. **Surgical patch** (only the 19 lines around `bitunix_observer = BitunixFuturesObserver(...)`) — see lessons-learned below.

**Features shipped:**
- Multi-bar confluence accumulation on bitunix_futures: signal weights survive their TTL windows (Otter 15-30 min, Cypher B 4h, Cypher A 24h, Bias 90 min, CVD 30 min). Score updates on every webhook arrival.
- Per-signal-name dedupe within TTL (repeated `mc_a_red_diamond` fires count once, most-recent wins).
- Same-direction cooldown gate (1800s) on top of cap math.
- `bitunix_signal_ledger` table accumulating real prod data — usable for re-tuning weights without code changes.
- `bitunix_score_decided` audit rows on every alert, with full score breakdown (`final_buy_score`, `final_sell_score`, `net_score`, `buy_contributions`, `sell_contributions`, `cooldown_blocked`, `reason`).

**Notable code changes:**
- Phase 3.1 `_tier_for` classifier is **fully bypassed when `scoring.enabled=True`** — score path replaces it (single open trade at a time, opposite-side signals do not auto-flip in v1; cooldown handles same-side). The old code remains in-place behind the flag for fast rollback.
- Price context in live mode is **signal-only for v1** — `PriceContext(pct_change=0, PA flags=False)`. Guards and PA factors (VWAP, HH/LL, volume) inert in prod. Backtest used them; gap is intentional and small (max ±4 score points). Phase 3.2.2 will wire `LiveBarCache` to compute PA factors live.
- Tier sizing (`TIER_SIZING` constants) shared between Phase 3.1 and 3.2. 0.5% effective-risk cap and 3% daily-kill enforced on the score path identically.

**Latent bugs caught + fixed:**
- `bitunix_futures_observer.py` import was missing `timedelta` (had `datetime`, `timezone` only) — caught in local E2E test before prod deploy.
- The score-path code uses `self._read_daily_risk` and `self._build_proposal` — both existed but were defined later in the class; Python resolves at call time, so no import-time impact.

**Verification:**
- md5 match on all 4 files post-scp ✓
- Prod-side `python -c 'import trading_corp.main; print("IMPORT OK")'` ✓
- Systemd active state ✓
- New tables created: `bitunix_signal_ledger` (0 rows at deploy), `bitunix_score_cooldown` (0 rows) ✓
- /healthz=200 after warm-up ✓
- Waiting on first webhook to confirm ledger append + score evaluation (real-data test)

**Lessons learned (load-bearing for future sessions):**
1. **Never `scp` a whole file when a surgical edit will do.** First deploy attempt scp'd my local `main.py` which had unrelated in-flight changes (`kalshi_copy_trader` import not yet shipped). Service crash-looped on `ModuleNotFoundError`. Recovery: rollback to backup tag, pull prod's `main.py` to local, `python` patch only the 19 lines we needed, scp back. Cost: ~3 minutes of restart noise, no data loss. The CLAUDE.md "filesystem-not-git scope" rule covers this — diff the file first, send only what changed.
2. **`btc_accumulator.py` was scaffold code that never shipped.** When `bitunix_confluence.py` imported from it, prod hit `ModuleNotFoundError` on the first restart. Pushed `btc_accumulator.py` to prod as the second-step recovery. Reasonable choice (small, pure-function, no side effects on import) but flagged here so future sessions know it's a dependency, not dead code.
3. **Crash-loop during 1st-attempt deploy was caught by systemd auto-restart** + the immediate `journalctl` check. Two restart cycles within 20s, no permanent state corruption (the new tables were created idempotently via `CREATE TABLE IF NOT EXISTS`).

**Inert / dormant on current traffic:**
- Price-action factors (`above_session_vwap`, `higher_highs_4h`, `lower_lows_4h`, `volume_above_20bar_avg`) — never evaluated in live mode (all flags=False). Will activate in Phase 3.2.2 when `LiveBarCache` gains the helpers.
- Guard penalties (`sell_on_rush`, `buy_on_fall`) — never fire in live mode (`pct_change_in_window_*=0`). Same Phase 3.2.2 dependency.
- `bitunix_futures.scoring.tier_thresholds.weak: 5` band — never fires because `min_score_to_fire=8` filters it out. Kept in YAML for tier-naming clarity and easy re-enable.
- Phase 3.1 `_tier_for` classifier + `_maybe_propose` — code retained, only reached when `scoring.enabled=False`.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-score-20260511-1747; BASE=/home/azureuser/trading_corp; \
mv \$BASE/config/strategies.yaml.\$TAG  \$BASE/config/strategies.yaml; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG  \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
TAG2=pre-bitunix-score-20260511-1747-v2; \
mv \$BASE/trading_corp/main.py.\$TAG2  \$BASE/trading_corp/main.py; \
rm -f \$BASE/trading_corp/agents/strategies/bitunix_confluence.py; \
rm -f \$BASE/trading_corp/agents/strategies/btc_accumulator.py; \
sudo systemctl restart trading-corp.service
"
```
(Notes: `strategies.yaml.$TAG` is from the first backup; `main.py.$TAG2` is from the post-recovery backup because the original `main.py.$TAG` was already moved during the rollback step. Removing the two NEW files cleans up; the two new tables in SQLite are kept — they're idempotent and harmless when unused.)

---

## 2026-05-11 07:00 UTC — Structural arb event_title in would_have_placed payload (two-deploy fix)

**Triggered by:** Open paper trades table on the dashboard's `kalshi_arbitrage` view showed raw tickers like `KXTRUMPRUN-28JAN01` in the Market column (gibberish to a human). The data exists — kalshi_temporal_bucket_arb and kalshi_tail_price_arb both carry `event.title` at scan time and DO include it in their `kalshi_*_evaluated` audit events — they just weren't propagating it into the `would_have_placed` payload.

**FIRST DEPLOY (07:00 UTC) — strategy code (2 modified, PID 222245):**
- `trading_corp/agents/strategies/kalshi_tail_price_arb.py` — added `"event_title": opp.title` to the `common_extra` dict at line 383.
- `trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py` — added `"event_title": opp.title` to both the temporal-arb `common` dict (~line 570) and the bucket-arb `common` dict (~line 614).

**Post-deploy verification revealed a SECOND bug:** new structural emits at 05:10:17 UTC still had no `event_title` in the audit payload. The strategies were correctly putting it in `ProposedOrder.extra`, but the orchestrator loops in `main.py` build the audit payload from a **fixed allowlist** of `ext.get(...)` keys — `event_title` wasn't in the allowlist, so it was silently dropped:

```python
base_payload = {
    "strategy": agent.name, ...
    "ticker": ext.get("ticker"),
    "event_ticker": ext.get("event_ticker"),
    # event_title NOT in allowlist — got dropped here
    ...
}
```

**SECOND DEPLOY (05:20 UTC) — main.py allowlist fix (PID 224389):**
- `trading_corp/main.py` — added `"event_title": ext.get("event_title")` to the `base_payload` allowlist in BOTH `_scheduled_kalshi_arb_loop` (line 1885) and `_scheduled_kalshi_tb_arb_loop` (line 2039). Same pattern as `event_ticker` — single key-add per loop.

**Backup tags:**
- `pre-structural-event-title-20260511-0700` (strategy files)
- `pre-event-title-mainpy-20260511-0520` (main.py allowlist)

**Lesson for future "field not landing in audit row" debugging:**
- ProposedOrder.extra is NOT a transparent passthrough into audit payloads. Each orchestrator loop (`_scheduled_kalshi_*_loop`, polymarket equivalent) has an explicit allowlist when building the `base_payload`. New fields need to be added at BOTH layers: the strategy file (where the value is computed) AND the main.py loop (where it gets routed into the audit event). Easy to miss because the strategy unit tests would pass — the field IS in extra; it just doesn't reach storage.

**Why this works without dashboard changes:** the dashboard template already prefers `event_title` over the bare ticker:

```jinja
{{ ot.market_title or ot.market_id }}
```

…and `_query_pm_open_trades` already populates `PMOpenTrade.market_title` from `p.get("event_title") or p.get("ticker")`. So the moment the strategy starts including `event_title` in its payload, the Market column auto-renders the title. No template / data-layer changes needed.

**Pre-deploy verification:**
- AST parse on both files.
- No new tests needed — existing tests don't assert ProposedOrder.extra contents at that level; the change is a single string-keyed addition to a dict that's already plumbed through. Verification happens post-deploy via real audit data.

**Post-deploy verification (prod):**
- PID rotated 221187 → 222245. Web up after 50s warm-up.
- File md5/grep confirmed `event_title` in both deployed files (1 new occurrence in tail, 2 new in temporal_bucket).
- Awaiting next 5-min scan tick (kalshi_temporal_bucket_arb + kalshi_tail_price_arb both poll on 300s cadence) to confirm fresh `would_have_placed` audit rows carry the field.

**Backward compatibility:**
- Existing 120+ pending structural arb rows in `audit_event` table still have payloads without `event_title` — dashboard falls back to ticker for those (template's `or ot.market_id` branch). New emissions from this restart forward will have readable titles.
- No schema change; no resolver change; no template change. Just enriched payload going forward.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-structural-event-title-20260511-0700; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py.\$TAG       \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py; \
mv \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py.\$TAG  \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 06:01 UTC — PM dashboard expandable rows + LLM analysis surfacing

**Triggered by:** User feedback after 05:02 UTC deploy:
1. "Kalshi Arbitrage bot descriptions could use some work" — structural arb (temporal/bucket/tail) rows showed just gibberish tickers like `KXTEMPNYCM-2026-S2`.
2. "Where is the detailed LLM analysis saved for the kalshi arbitrage bot details? Is there a way to have this information show up on each row?" — kalshi_llm has rich `llm_reasoning` + `key_unknowns` + `llm_confidence` in the audit payload; dashboard wasn't surfacing it.

**Files deployed (4 modified):**
- `trading_corp/web/data.py`:
  - **PMOpenTrade** gained `rationale`, `llm_reasoning`, `key_unknowns`, `llm_confidence`, `subtitle`, `leg_date`. Parsed from the would_have_placed payload in `_query_pm_open_trades` (LLM strategies populate everything; structural strategies populate rationale + leg_date).
  - **PMRoundTrip** gained `rationale`, `llm_reasoning`, `key_unknowns`, `llm_confidence`, `subtitle`. Parsed from `kalshi_round_trips.extra_json` in `_query_pm_round_trips`. `extra_json` column added to the SELECT (was missing).
  - **Defensive parsing**: malformed `extra_json` strings + missing `key_unknowns` list fields all default cleanly to `None` / `[]`.
  - Polymarket round-trips don't yet store `extra_json` (different schema), so polymarket PMRoundTrip rows get `None` for the analysis fields. Future polymarket resolver enrichment can fill these in.
- `trading_corp/agents/kalshi_resolver.py` — `_compute_round_trip_row` now serializes `llm_reasoning` and `key_unknowns` (plus the existing `llm_confidence` and `rationale`) into `extra_json` so future kalshi_round_trips rows carry the full analysis. Pre-2026-05-11 ~05:30 UTC rows just have None for these fields — they render a clear "no detailed analysis stored" message in the expand panel.
- `trading_corp/web/templates/partials/pm_dashboard_body.html`:
  - **Open tab table**: every row is now click-to-expand. Added a leading caret column (▸ / ▾) + `pm-expand-trigger` class with `data-pm-detail="ot-{i}"`. Below each row sits a hidden `<tr class="pm-detail-row hidden">` with a 3-column grid (Trade context · Analysis · ...). Columns swap dynamically: dropped the "Cost" column from the main row (moved into expand panel) to make room for the wider Market column.
  - **History tab table**: same expandable pattern with `rt-{i}` ids + Analysis section that includes implied @ entry + LLM prob + analysis text. Existing wins/losses filter buttons preserved.
  - **Analysis section contents**: rationale (always shown when present), full LLM reasoning (whitespace-preserved), Key unknowns bullet list, confidence pill (low/medium/high color-coded), subtitle (kalshi sub-title like "-1° or below"). Structural arb rows show the rationale + leg date; LLM rows show everything.
  - **Trade context section**: market title, ticker, sub-title, category, leg date, strategy, cost, order ID.
- `trading_corp/web/templates/prediction_markets_dashboard.html` — added expand-trigger handler to the delegated click listener (lives outside the swap target so it persists across HTMX swaps). Toggles the matching `#pm-detail-{id}` row's `hidden` class + flips the caret glyph.

**Backup tag:** `pre-pm-analysis-rows-20260511-0600`

**Pre-deploy verification:**
- 5 new tests covering: LLM reasoning parsing from open-trades payload, structural arb rationale-without-LLM, round-trip parses extra_json analysis fields, legacy empty extra_json, malformed extra_json.
- 28 PM dashboard tests pass; 78 total polymarket + kalshi + dashboard tests pass; zero regressions.
- AST + Jinja parse on all modified files; drift check on prod showed clean additive diffs.

**Post-deploy verification (prod):**
- PID rotated 219957 → 221187. Web up after 50s warm-up.
- All routes return 200; partial route stays fast at ~30ms.
- HTML inspection confirms:
  - All open-trade rows render with `pm-expand-trigger` class + detail rows below.
  - kalshi_llm row 0 expand panel shows "medium confidence" pill + reasoning text + "Key unknowns" bulleted list.
  - kalshi_arbitrage (structural) row 0 expand panel shows ticker + leg date + strategy + cost + order ID + structural rationale ("Temporal arb on KXTRUMPRUN...").

**Notable code decisions:**
- **Delegated click handler for expansion** (not inline `onclick`). Same pattern as the tab + filter handlers — single listener on `document`, survives every HTMX swap. The swapped-in rows just need the correct `data-pm-detail` attribute.
- **Single template for both LLM and structural rows.** The analysis section uses `{% if rt.rationale %}` / `{% if rt.llm_reasoning %}` guards so the same template renders cleanly for any strategy. Empty cases get a plain "No detailed analysis stored" message instead of awkward gaps.
- **Resolver enriched FORWARD only**, not backfilled. The single existing kalshi_round_trips row (the K2.4 NYC-temp loss) doesn't have llm_reasoning in its extra_json — re-resolving requires deleting + waiting for the next hourly tick. Not worth it for one row. New rows from now on carry the full analysis.
- **`extra_json` column added to the SELECT**. Subtle bug — the old query omitted it, so the new fields-from-extra-json parsing silently returned None for all rows. Caught by tests before deploy.

**Known gap (separate follow-up):**
- Structural arb strategies (`kalshi_tail_price_arb`, `kalshi_temporal_bucket_arb`) don't put `event_title` in their would_have_placed payloads — so the Market column shows raw tickers like `KXTRUMPRUN-28JAN01` instead of human-readable titles. The data exists in the discovery layer at emit time; small strategy-code edit needed. Tracking this as a future tile-readability pass.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-pm-analysis-rows-20260511-0600; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG                                                          \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/agents/kalshi_resolver.py.\$TAG                                            \$BASE/trading_corp/agents/kalshi_resolver.py; \
mv \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html.\$TAG                      \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html; \
mv \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html.\$TAG                        \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 05:02 UTC — PM dashboard fixes (HTMX swap + Open trades tab + kalshi_copy_trading)

**Triggered by:** User-reported issues with the dashboard shipped 04:04 UTC:
1. **60-70s page blank on every division switch** — dropdown `onchange="window.location.href=..."` did a full page nav. Every full nav passes through Authelia forward_auth in Caddy → re-auth + redirect chain → slow.
2. **`would_have_placed` paper trades not visible** — only the count showed (`Pending: 123`); user wanted to see the trades themselves.
3. **`kalshi_copy_trading` missing from dropdown** — divisions.yaml didn't have the entry yet (waiting on K3); the dashboard is divisions-list-driven so nothing to show.

**Files deployed (3 modified, 1 new):**
- `trading_corp/web/data.py` — added `PMOpenTrade` dataclass + `_query_pm_open_trades(db_url, slugs, limit)` (cross-venue UNION on `audit_event WHERE kind='would_have_placed'` LEFT JOIN round-trip tables, excludes resolved). `build_prediction_market_view` now fans 3 queries (round_trips + equity_curve + open_trades); `summary.n_pending = len(open_trades)` so the card count and the table can't drift apart. Side detection in `_query_pm_open_trades` reuses the same outcome/leg-prefix fallback ladder as the resolver. Removed an unused `placeholders` variable in `_query_pm_round_trips`.
- `trading_corp/web/routes.py` — added partial endpoints `GET /partials/prediction-markets/{division?}` that render JUST `partials/pm_dashboard_body.html` (no base.html chrome). Crucially, the partial handler **skips `build_command_center`** — the corp-wide snap is only needed for the base header/footer, which the partial doesn't include. That's what makes the swap fast (23ms vs 2.7s for the full page).
- `config/divisions.yaml` — added `kalshi_copy_trading` (broker: paper, standby: true, enabled: true), mirroring the polymarket_copy_trading placeholder pattern. K3 will flip standby:false when the leaderboard scraper + copy-trader strategy ship. The division now appears in the dashboard dropdown, the home-page tile group, and any future cross-venue queries automatically include it.
- `trading_corp/web/templates/prediction_markets_dashboard.html` — restructured into a thin shell: header + dropdown + `<div id="pm-content">{% include "partials/pm_dashboard_body.html" %}</div>` + a script tag that wires HTMX swap on the dropdown's `change` event. Tab and history-filter handlers moved to delegated `document` click listeners so they survive every HTMX swap (the swapped DOM nodes re-bind automatically). `popstate` handler keeps back/forward button correct. `htmx:afterSwap` listener calls `window.renderPMChart()` to re-create the equity chart on the new container. Fall-through to full nav if HTMX is unavailable.
- **NEW:** `trading_corp/web/templates/partials/pm_dashboard_body.html` — everything that changes between divisions: selected-label sub-header, 6 summary cards, **3-tab nav (Portfolio + OPEN + History)**, portfolio + open + history panels, inline equity-curve JSON. Used both by the full-page render and by the HTMX swap endpoint.
- `trading_corp/web/static/js/prediction_markets_chart.js` — refactored from one-shot IIFE to expose `window.renderPMChart()`. Disposes any prior chart instance + ResizeObserver before creating fresh ones — needed because the chart container DOM node is replaced on every HTMX swap.

**Open tab columns:** emitted ts · age (m/h/d) · [division — in All-mode only] · venue · market title · side · qty · entry · cost · signal (divergence % or edge ¢) · resolves-at.

**Backup tag:** `pre-pm-dashboard-htmx-20260511-0500`

**Pre-deploy verification:**
- AST parse + jinja parse on all modified/new files.
- 5 new tests covering open-trades query: LLM-payload normalization, temporal/bucket leg-prefix parsing, polymarket payload, resolved-exclusion, All-mode UNION + sort. **23 PM dashboard tests pass; 92 total polymarket + kalshi + dashboard tests pass; zero regressions.**
- Prod-drift check: all 3 modified files matched my last patched-prod content + my new patches (clean additive diff — verified line-by-line for each file).

**Post-deploy verification (prod):**
- PID rotated 217797 → 219957. Web up after 50s warm-up.
- **Speed:** full-page route `/prediction-markets/kalshi_llm_arbitrage` = 2.68s; partial route `/partials/prediction-markets/kalshi_llm_arbitrage` = **23ms** (116× faster). Dropdown switches no longer trigger full nav through Authelia, so user-perceived blank-screen time drops from 60-70s to sub-second.
- All 5 prediction-market divisions appear in the dropdown: All / Polymarket Arbitrage / Polymarket Copy Trading / Kalshi Arbitrage / Kalshi LLM Arbitrage / **Kalshi Copy Trading** (new).
- Home page now links to `/prediction-markets/{slug}` for all 5 divisions.
- 3-tab dashboard renders with Portfolio + OPEN + History tabs; Open tab shows pending paper-trade table populated from the live audit-event data.

**Notable code decisions:**
- **HTMX over full nav** is the architecturally right answer regardless of Authelia. In-app navigation between divisions of the SAME dashboard shouldn't re-fetch the corp-wide header/footer; partial swap is correct semantics + much faster.
- **`build_command_center` skipped on partial endpoint.** This is the single biggest contributor to the speed gain — broker.snapshot fan-out across all divisions (especially Fidelity selenium) is the slow part. The partial doesn't need it because the page header/footer don't change.
- **Delegated event listeners on `document`.** Tab and filter buttons live inside the swappable region; per-element listeners would die on every swap. The delegated handler binds once on the outer scope and works for every swap iteration.
- **`window.renderPMChart()` exposed globally + disposal-before-render.** Lightweight Charts needs explicit `.remove()` on the old chart before creating a new one on a fresh DOM node. The chart's ResizeObserver is also disposed to avoid orphaned observers piling up.
- **`open_trades` and `n_pending` share one source of truth.** Summary card and table can't drift — both come from the same query result.
- **kalshi_copy_trading as standby placeholder.** Division registry-driven dashboard means future K3 work doesn't touch the dashboard layer; flipping `standby: false` is sufficient when the strategy ships.

**Inert / dormant on current traffic:**
- Open trades tab shows 123 pending for kalshi_llm_arbitrage (matches DB state). New pending trades from the active scanners appear here automatically as their `would_have_placed` rows land.
- kalshi_copy_trading division shows zero state (no strategy writing to it). When K3 ships, its data populates without dashboard changes.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-pm-dashboard-htmx-20260511-0500; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG                                                                \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/routes.py.\$TAG                                                              \$BASE/trading_corp/web/routes.py; \
mv \$BASE/config/divisions.yaml.\$TAG                                                                   \$BASE/config/divisions.yaml; \
mv \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html.\$TAG                            \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html; \
mv \$BASE/trading_corp/web/static/js/prediction_markets_chart.js.\$TAG                                  \$BASE/trading_corp/web/static/js/prediction_markets_chart.js; \
rm \$BASE/trading_corp/web/templates/partials/pm_dashboard_body.html; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 04:04 UTC — Prediction Markets dashboard (K2.4 Option C)

**Triggered by:** User vision lock-in for the prediction-markets surface. Single dashboard at `/prediction-markets/{division?}` parameterized by division. Tiles on the home page get a performance overview (win rate / resolved / pending / realized) and link directly to the dashboard with the division pre-selected. Same template + tabs for every division; dropdown switches the data. "All Prediction Markets" combined view aggregates across all 4 (later 5) divisions. Scope is "Option C" — Portfolio + History tabs only; Positions/Activity/Report tabs deferred until data densifies. Forward-compatible: future `kalshi_copy_trading` (Phase K3) auto-appears in the dropdown the moment it registers in `divisions.yaml`.

**Files deployed (4 modified, 3 new):**
- `trading_corp/web/data.py` — added the prediction-markets dashboard data layer (~390 lines):
  - **5 new dataclasses:** `PMRoundTrip`, `PMEquityPoint`, `PMSummary`, `PMDivisionOption`, `PMDashboardView`.
  - **Cross-venue helpers:** `_pm_venue(slug)` (kalshi vs polymarket inference), `_pm_divisions_all()` (filter from divisions.yaml).
  - **3 query functions:** `_query_pm_round_trips` (UNIONs `polymarket_round_trips` + `kalshi_round_trips`, normalizes to PMRoundTrip), `_query_pm_equity_curve` (cross-venue equity snapshots), `_query_pm_pending_count` (would_have_placed rows without resolution row).
  - **2 aggregators:** `_pm_equity_at(curve, ts)` (last-equity lookup, sums across divisions for All mode), `_pm_summary` (computes summary cards; voids excluded from win rate denominator).
  - **Entry point:** `build_prediction_market_view(deps, division)` — `division=None` for All mode, returns None for unknown slug (route turns into 404). Fans 3 queries via `asyncio.to_thread`.
  - **Home-tile hydration:** new `_hydrate_pm_overview(divisions, db_url)` — single sweep, three aggregate queries; attaches `pm_overview` dict to each prediction-market division. Called from `build_command_center` after the donchian hydration block.
- `trading_corp/web/routes.py` — added `GET /prediction-markets/` and `GET /prediction-markets/{division}` routes. Both go through `_render_pm_dashboard(request, division)` which fans `build_command_center` + `build_prediction_market_view` in parallel. Returns 404 on unknown division. Old `/division/{slug}` route untouched (legacy access still works for the 4 prediction-market divisions).
- `trading_corp/utils/divisions.py` — added `pm_overview: dict | None = None` field to the `Division` dataclass for the home-tile hydration target.
- `trading_corp/web/templates/home.html` — tiles in the `prediction_markets` investment group now link to `/prediction-markets/{slug}` (not `/division/{slug}`) and render an inline performance overview (win % · resolved · pending counters + realized P&L row) when `d.pm_overview` is populated. Other groups (Individual / Crypto / Retirement) unchanged.
- **NEW:** `trading_corp/web/templates/prediction_markets_dashboard.html` — single template with header bar (← Command Center · Prediction Markets — <label> · division dropdown), 6 summary cards (Equity / Today's P&L / Win rate / Resolved / Pending / Realized), 2-tab nav (Portfolio + History; vanilla JS toggle, no HTMX). Portfolio tab = equity-curve chart container + outcome-breakdown sidebar. History tab = resolved-markets table with venue badge, market title, side, qty, entry, result, P&L, ROI; in All-mode adds a Division column. Wins/Losses/All filter buttons toggle row visibility via `pm-history-row[data-won]` attribute.
- **NEW:** `trading_corp/web/static/js/prediction_markets_chart.js` — Lightweight Charts wiring for the equity curve. Reads inline JSON from `#pm-equity-data` (server-rendered, no HTTP fetch). In All mode it aggregates per-timestamp across divisions (sum of equity per unique 5-min epoch). Resilient empty-state.

**Backup tag:** `pre-pm-dashboard-20260511-0410`

**Pre-deploy verification:**
- AST parse on all 3 modified Python files + Jinja parse on `prediction_markets_dashboard.html` + `home.html`.
- **18 new tests in `tests/test_prediction_markets_dashboard.py`** covering: venue inference, cross-venue UNION query + normalization, division-filtered queries, equity-curve cutoff, pending-count cross-venue, summary win-rate math (voids excluded), tile hydration (only touches prediction-market divisions), invalid-slug → None, All mode aggregates correctly.
- 87 polymarket + kalshi_resolver + backtest_polymarket + prediction_markets tests combined pass; zero regressions.
- Prod-drift check: prod's `data.py`, `routes.py`, `divisions.py`, `home.html` all had md5s differing from local HEAD. All 4 patches applied onto PROD content via the `/tmp/k24_prod/*.patched` workflow (memory `trading_corp_prod_git_drift`). Anchor strings verified before each edit.

**Post-deploy verification (prod):**
- PID rotated 215310 → 217797. Service active; web server up on port 8000 after the usual 30s warm-up (Fidelity bot-detection check is the bottleneck on cold start — pre-existing).
- HTTP smoke test — all expected status codes:
  - `GET /` → 200 (home page)
  - `GET /prediction-markets/` → 200 (All mode)
  - `GET /prediction-markets/kalshi_llm_arbitrage` → 200
  - `GET /prediction-markets/kalshi_arbitrage` → 200
  - `GET /prediction-markets/polymarket_arbitrage` → 200
  - `GET /prediction-markets/not-real` → **404** (correct)
  - `GET /static/js/prediction_markets_chart.js` → 200
- Home-page content check: 4 `/prediction-markets/` links found in tile group (one per active prediction-market division). 
- Kalshi LLM dashboard at `/prediction-markets/kalshi_llm_arbitrage` shows **1 Resolved · 121 Pending** in the summary cards — matches DB state (1 row in kalshi_round_trips from the K2.4 resolver tick + 121 unresolved would_have_placed entries).
- Dropdown selected-option check: `selected` attribute lands on "All Prediction Markets" at `/prediction-markets/` and on "kalshi_llm_arbitrage" at the slug URL.

**Notable code decisions:**
- **One template, one route, one builder.** Cross-venue normalization happens at the data layer; the template is venue-agnostic except for a small venue badge in the History tab.
- **Vanilla-JS tab toggle, not HTMX.** Tab content is small and pre-rendered server-side — no need for an extra round-trip. Keeps the dashboard fast on first paint and simple to reason about.
- **Equity-curve data inlined as JSON.** Avoids a second HTTP round-trip; the chart paints instantly once Lightweight Charts loads. In All mode the JS sums per-timestamp.
- **Division dropdown is full-nav (not HTMX swap).** Bookmark + back button work correctly; the URL is the source of truth for which division is selected.
- **Voids excluded from win-rate denominator.** Per K2.4 P&L semantics, void markets refund — they're not wins or losses. Win rate = wins / (wins + losses), voids tallied separately.
- **`Division.pm_overview` attribute, dict not dataclass.** Mirrors the existing `Division.donchian` shape. Keeps the home tile template branch-free (just check truthiness) without dragging more dataclass schema across module boundaries.
- **Polymarket round-trips have no `division` column.** Today only `polymarket_arbitrage` writes them; we accept that filter assumption explicitly in `_query_pm_round_trips` and `_query_pm_pending_count`. When `polymarket_copy_trading` ships its own resolver path, either add a `division` column to `polymarket_round_trips` or write to a separate table.
- **Forward-compat for kalshi_copy_trading (Phase K3).** Dropdown reads `load_divisions()` live — when K3 ships and adds the new division to `divisions.yaml`, it auto-appears. The venue inference (`kalshi_` prefix → "kalshi") and the query layer (queries kalshi tables when any `kalshi_*` slug is in the filter) already handle it; only the round-trips/equity tables need K3's strategy to write rows.

**Inert / dormant on current traffic:**
- Round-trips count is low (1 resolved row total) so the History tab is sparse and the equity curve has ~30 minutes of data points. Both grow over time as resolver ticks land + 5-min snapshots accumulate. The dashboard renders cleanly at this density — empty-state messages cover the zero-data edge.
- Positions/Activity/Report tabs are deferred — not yet built. Adding them later is additive (new partials, new dropdown items in the tab nav) and doesn't reshape the data layer.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-pm-dashboard-20260511-0410; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG                  \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/routes.py.\$TAG                \$BASE/trading_corp/web/routes.py; \
mv \$BASE/trading_corp/utils/divisions.py.\$TAG           \$BASE/trading_corp/utils/divisions.py; \
mv \$BASE/trading_corp/web/templates/home.html.\$TAG      \$BASE/trading_corp/web/templates/home.html; \
rm \$BASE/trading_corp/web/templates/prediction_markets_dashboard.html; \
rm \$BASE/trading_corp/web/static/js/prediction_markets_chart.js; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 03:23 UTC — Kalshi Phase K7 + tune A (polymarket semaphore + lift time horizon 7d→14d)

**Triggered by:** Session-start audit decision tree, branch "B then A" picked by Board after diagnosis. K7 puts a defensive cap on polymarket's K=20 LLM fan; tune A lifts polymarket's `time_horizon_max_days` from 7 → 14 to resurrect survivor counts (was hitting 0/cycle pre-tune). Both shipped in sequence — semaphore first (insurance), tune second (load).

**Background — why both:**
- Polymarket had 0 survivors per scan cycle for the last hour pre-deploy (universe of 46–48 markets entirely filtered out by 7d horizon + 6h cooldown saturation). Lifting the horizon was the only way to revive evaluation.
- Resurrecting polymarket evaluation re-introduces the original 429-risk pattern (parallel K=20 fan overlapping kalshi_llm K=20). Kalshi has had Semaphore(8) since 01:08 UTC; polymarket was still uncapped. Insurance first.
- Kalshi LLM 15-30d bucket showed 26% avg divergence — comparable signal quality to ≤7d's 27%. So extending polymarket modestly to 14d is consistent with where Kalshi finds signal. Not jumping all the way to 30d.

**Files deployed (2 modified):**
- `trading_corp/agents/strategies/polymarket_arbitrage.py` — `run_scan_cycle()`'s warm-and-fan block now wraps `_estimate_probability` in `_gated_estimate` using `asyncio.Semaphore(llm_concurrency)`. Default 8 per memory `anthropic_concurrent_connections.md`. Both the warm call (`survivors[0]`) and the K-1 parallel fan go through the gate. Failed Anthropic requests still return None and advance cooldowns; semaphore releases on exception. Mirrors `kalshi_llm_arbitrage`'s pattern verbatim.
- `config/strategies.yaml` — polymarket_arbitrage block:
  - Added `llm_concurrency: 8` with explanatory comment.
  - Changed `time_horizon_max_days: 7` → `14`. Comment notes the K2.4 retune rationale.

**Backup tag:** `pre-kalshi-k7-polysemaphore-20260511-0325`

**Pre-deploy verification:**
- AST parse on patched-prod file. YAML loads correctly with both polymarket + kalshi `llm_concurrency=8` and polymarket `time_horizon_max_days=14`.
- 2 new functional tests in `tests/test_polymarket_arbitrage.py`:
  - `test_llm_fan_capped_by_semaphore`: K=10 survivors, `llm_concurrency=3` → asserts peak concurrent ≤ 3 via lock + counter spy on `_estimate_probability`.
  - `test_llm_fan_default_semaphore_is_8`: K=20 survivors, no `llm_concurrency` key → asserts peak ≤ 8 (default).
- 69 polymarket + kalshi_resolver + backtest_polymarket tests pass; zero regressions.
- Prod-drift check: prod's `polymarket_arbitrage.py` md5 differed from local HEAD; prod's `strategies.yaml` md5 differed from local HEAD. Both patches applied onto PROD's content via `/tmp/k24_prod/*.patched` workflow.

**Post-deploy verification (prod):**
- PID rotated 213825 → 215310; service active.
- Startup log shows all 4 scanners + 3 K2.4 background tasks online cleanly. No tracebacks related to K7. (Pre-existing Fidelity bot-detection error is unchanged noise.)
- **A took effect on next polymarket cycle (03:22:49 UTC):** `markets_pre_filter` jumped 47 → 56 (the 7–14d horizon markets surfaced via gamma-api end_date_max parameter); `survivors_post_filter` jumped 0 → 2.
- **First post-tune LLM calls (03:23:03 UTC):** 2 polymarket markets evaluated — Trump-related politics market + ATP tennis match (both within 14d window). Zero 429s. Semaphore well under cap (peak 2 vs ceiling 8). Strategy producing signal again after going dark for ~hours.
- `strategies.yaml` is hot-reloaded each scan cycle via `_reload()` — no restart was needed for tune A; the polymarket loop picked it up within 30s. K7 code IS in the restarted process, picked up by every cycle starting at 03:21:48.

**Notable code decisions:**
- **Semaphore on BOTH calls** (warm + fan), not just the fan. The warm call is single — semaphore allows it instantly — but wrapping it keeps `_gated_estimate` the only path to `_estimate_probability` so future maintenance can't accidentally bypass the cap.
- **Default 8 matches kalshi.** A future shared LLM-client-layer semaphore would be the architecturally cleaner cap (one global pool); deferred since both strategies are independently capped now and 429s are gone.
- **Tune-A bumped 7d → 14d, not 30d.** Conservative step. Kalshi LLM's 31-60d bucket has 0 trades overnight (the cap binds there too) — there's a natural plateau where longer horizons stop adding signal. 14d resurrects polymarket without overshooting.

**Inert / dormant on current traffic:**
- Polymarket cooldown saturates fast — after the first round of evaluations, expect `survivors_post_filter` to drop back to single digits or 0 for ~6h until cooldowns expire. That's by design; semaphore is the insurance for when bursts return.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k7-polysemaphore-20260511-0325; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/polymarket_arbitrage.py.\$TAG \$BASE/trading_corp/agents/strategies/polymarket_arbitrage.py; \
mv \$BASE/config/strategies.yaml.\$TAG                                  \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 03:06 UTC — Kalshi Phase K2.4 (round-trip resolver + equity snapshot data layer)

**Triggered by:** Session-start audit showed 92 kalshi paper trades overnight (82 LLM + 10 structural temporal/bucket) accumulating without resolution. Decision-tree branch ">30 would_have_placed → ship K2.4 first" applied. Closes the data-layer gap noted in BACKLOG P0 NEXT: both Kalshi divisions previously showed only the $499 broker balance and no historical PnL — paper trades fired but no round-trip resolution existed to surface win/loss expectancy.

**Files deployed (3 modified, 1 new):**
- `trading_corp/persistence/db.py` — added 2 tables to SCHEMA:
  - `kalshi_round_trips` — single table covering all three Kalshi strategies (tail/temporal-bucket/llm); columns capture ticker + event_ticker + strategy + division + arb_type + arb_set_id + outcome_bet + qty/price/notional + entry/resolved ts + market_result + won/realized_pnl/roi_pct + implied_at_entry + llm_prob + divergence_pct + edge_cents + extra_json. UNIQUE(order_id) so resolver re-runs are safe.
  - `kalshi_equity_history` — per-division 5-min equity snapshots; columns ts + division + equity + cash_usd + positions_value + n_positions. Both Kalshi divisions share the same broker today so snapshots reflect identical dollar values; per-division separation preserves dashboard logical grouping and is forward-compatible with a future per-division sub-account split.
- `trading_corp/brokers/kalshi.py` — added `KalshiBroker.get_market_resolution(ticker)` async method. Looks up market via `client.get_market(ticker)`, reads `.result` field (Kalshi sets to "yes"/"no" at settlement, "void" for cancelled markets, "" while in-flight). Returns `{status: resolved|pending|void|not_found, result, ticker, close_time}`. Stub mode returns `not_found` (caller skips).
- `trading_corp/main.py` — wired 3 new asyncio tasks after the polymarket resolver block: `kalshi_resolver_task` (1h cadence) + `kalshi_equity_task_arb` (5min, kalshi_arbitrage division) + `kalshi_equity_task_llm` (5min, kalshi_llm_arbitrage division). All three cancellation hooks added in shutdown path via a small loop. Each guarded by `data_exec.brokers.get(division)` — if no broker is registered the task is skipped, never crashes startup.
- `trading_corp/agents/kalshi_resolver.py` — **NEW.** Structural clone of `polymarket_resolver.py` with Kalshi adapter:
  - `_fetch_unresolved_orders`: LEFT JOIN audit_event vs. kalshi_round_trips, keyed on `actor IN (kalshi_tail_price_arb, kalshi_temporal_bucket_arb, kalshi_llm_arbitrage)` AND `kind='would_have_placed'` AND no existing round-trip row.
  - `_detect_side(row)`: fallback ladder — `outcome` (LLM strategy) → `leg` prefix (`yes_*`/`no_*` for temporal_bucket, bare `yes`/`no` for tail_price). Returns 'yes', 'no', or None.
  - `_compute_round_trip_row`: Kalshi binary contracts pay $1 winner / $0 loser. Won → `qty × (1 - price)`. Lost → `-qty × price`. Void → 0. Skips malformed rows (price ≤0, price ≥1, qty ≤0, undetectable side).
  - `_insert_round_trip`: INSERT OR IGNORE keyed on order_id (re-run-safe).
  - `resolve_pending_round_trips(broker, max_per_tick=200)`: one pass; returns `{scanned, resolved, pending, void, not_found, errors}`. `max_per_tick=200` doubled vs polymarket because three Kalshi strategies share the table.
  - `write_equity_snapshot(db_url, division, broker)`: single snapshot per division.
  - `_resolver_loop` / `_equity_snapshot_loop`: periodic drivers, log-on-error-continue, asyncio.CancelledError clean exit.

**Backup tag:** `pre-kalshi-k24-resolver-20260511-0240`

**Pre-deploy verification:**
- AST parse on all 3 patched-prod files + new kalshi_resolver.py.
- 21 new kalshi_resolver tests pass (side detection × all 3 strategies, P&L math win/loss/void/malformed, INSERT OR IGNORE re-run safety, equity snapshot row shape + broker-error guard).
- 67 polymarket + kalshi_resolver tests combined pass; zero regressions.
- Prod-drift check: prod's `db.py` (331 lines vs local HEAD's 275) had extra helper functions; prod's `main.py` had bitunix_observer wiring not in local HEAD; prod's `kalshi.py` was untracked locally. All 3 patches applied onto PROD's content, not local HEAD, per the `trading_corp_prod_git_drift` memory note.

**Post-deploy verification (prod):**
- PID rotated 210117 → 213839; `systemctl is-active trading-corp` = `active`.
- `kalshi_round_trips` + `kalshi_equity_history` tables created with all expected columns + 3 indexes.
- Startup log: 3 new loops online — `kalshi round-trip resolver online (interval=3600s)` + `kalshi equity snapshot writer online (division=kalshi_arbitrage, interval=300s)` + `kalshi equity snapshot writer online (division=kalshi_llm_arbitrage, interval=300s)`.
- First equity snapshots landed at 03:06:26 UTC: both divisions at $499 cash / $0 positions / 0 n_positions.
- First resolver tick at 03:06:30 UTC: **scanned=113, resolved=1, pending=112, void=0, not_found=0, errors=0**. The 1 resolved row: order `a84388b6...` from kalshi_llm_arbitrage on `KXTEMPNYCH-26MAY1022-T64.99`, LLM bet NO @ $0.35 × 2.857 qty → market resolved YES → realized_pnl = -$1.00 (-100% ROI). Matches the strategy's $1/leg fixed sizing exactly.

**Notable code decisions:**
- **Single shared kalshi_round_trips table for all 3 strategies** (vs. one table per strategy). `strategy` + `arb_type` columns enable filtering. Avoids 3× schema duplication; future K4 multi-outcome detector adds rows with `arb_type='multi_outcome'` without DDL changes.
- **Side-detection via fallback ladder** (outcome → leg prefix). Each strategy serializes side differently in ProposedOrder.extra; resolver normalizes at read time. Tested across all three shapes.
- **Fees NOT modeled in paper-mode PnL.** Kalshi taker fee `roundup(0.07 × C × P × (1−C))` is mechanical but small relative to $1/leg sizing — gross PnL is the expectancy signal for paper-vs-live decisioning. Fees come in at Phase K5+ live work.
- **Two equity snapshots per cycle, identical dollars today.** Both divisions read the same Kalshi broker. Each writes its own row keyed by division — dashboard groups cleanly, and a future per-division sub-account split needs no schema change.
- **No HITL bypass risk.** This is read-only enrichment. No code path here touches order placement, risk caps, or live broker calls. Failure → log + skip + retry next tick.

**Inert / dormant on current traffic:**
- Resolver only acts on settled markets. Of 113 currently-unresolved kalshi paper trades, 112 are still in-flight (expiration dates ranging June 2026 and later). Resolver re-checks every hour — count will grow as markets settle.
- Dashboard surfacing of kalshi_round_trips + equity-curve sparkline is **NOT shipped here**; this is data layer only. Becomes the natural follow-up once round-trip counts grow past trivial. Polymarket's surfacing is also still TBD — they'd benefit from a shared partial.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k24-resolver-20260511-0240; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/persistence/db.py.\$TAG \$BASE/trading_corp/persistence/db.py; \
mv \$BASE/trading_corp/brokers/kalshi.py.\$TAG \$BASE/trading_corp/brokers/kalshi.py; \
mv \$BASE/trading_corp/main.py.\$TAG          \$BASE/trading_corp/main.py; \
rm \$BASE/trading_corp/agents/kalshi_resolver.py; \
sudo systemctl restart trading-corp
"
# The two new tables remain in the DB after rollback — they're harmless without
# the resolver code; sqlite drop is optional.
```

---

## 2026-05-11 00:52 UTC — Kalshi Phase K6.1 (LLM-divergence strategy, mirroring polymarket)

**Triggered by:** Board ask — "create another kalshi division that is LLM-based reusing what we built for polymarket." Phase K6.1 spins up a third Kalshi strategy (after structural tail + temporal/bucket already shipped) using the same LLM substrate as polymarket_arbitrage. Lives on its own division so dashboard surfaces it separately and risk caps are independent.

**Files deployed (6 modified, 1 new):**
- `trading_corp/agents/strategies/kalshi_llm_arbitrage.py` — **NEW.** `KalshiLLMArbitrageAgent` class. Clone of `PolymarketArbitrageAgent` with the Kalshi adapter:
  - Discovery via `KalshiBroker.list_markets()` (cache-aware; shared with the structural arb strategies' discovery cache)
  - Pre-filter: skip COLLECTION events, skip extreme-tail markets (already handled by `kalshi_tail_price_arb`), enforce min/max implied prob bounds + max time-to-resolution
  - K=20 markets per cycle, ranked by tightest spread first (LLM call most useful where market is least sure)
  - **Reuses `_polymarket_prompts.ANALYST_SYSTEM_PROMPT`** — generic enough for cross-venue prediction-market work, though category priors are polymarket-tuned (will revisit if Kalshi-specific priors materially help)
  - Warm-and-fan parallel LLM pattern: serial first call to hydrate Anthropic prompt cache, K-1 parallel after
  - Per-ticker 6h cooldown persisted in `agent_state` table (parallel to polymarket's per-condition_id cooldown)
  - ProposedOrder shape: `BUY YES @ yes_ask` if LLM thinks YES underpriced, `BUY NO @ no_ask` if overpriced. Fixed-USD sizing (default $1/leg).
- `trading_corp/main.py` — added `KalshiLLMArbitrageAgent` instantiation + `_scheduled_kalshi_llm_arb_loop` (clone of polymarket loop with name swap). Cancellation hook in shutdown path. Loop polls every 60s when enabled (matches polymarket cadence).
- `config/divisions.yaml` — new `kalshi_llm_arbitrage` division entry. Same Prediction Markets group, same kalshi broker (read-only). `standby: true` until first paper trades validate.
- `config/strategies.yaml` — new `kalshi_llm_arbitrage:` config block. K=20, cooldown 6h, divergence threshold 10%, time horizon 30d (broader than polymarket's 7d — Kalshi has many longer-horizon markets), prob bounds 0.05-0.95.
- `trading_corp/web/data.py` — added 3 new event kinds to SQL whitelist (`kalshi_llm_scan_cycle`, `kalshi_llm_probability_called`, `kalshi_llm_order_rejected_by_risk`) + `evt.kalshi_llm` enrichment dict mirroring polymarket's shape so the rich rail UI can render kalshi_llm rows with LLM probability strip + reasoning preview.
- `trading_corp/web/templates/division.html` — added `{% elif evt.kalshi_llm %}` branches for both kind label and body rendering. Same layout as polymarket: ticker badge + outcome badge + category + event title + LLM/market/divergence strip + reasoning preview + "Show analysis →" button.
- `trading_corp/web/routes.py` — new `GET /partials/kalshi-llm-analysis/{event_id}` HTMX endpoint. Reuses `partials/polymarket_analysis.html` (field name mapping: ticker→market_slug, event_title→market_question, expires_at→resolves_at, event_ticker→condition_id). Same right-rail rich panel.

**Backup tag:** `pre-kalshi-k61-llm-20260511-0048`

**Pre-deploy verification:**
- All 5 affected Python files parse cleanly (AST + Jinja).
- 66 polymarket/kalshi/main/risk tests pass; zero regressions.
- Local division registry verified: 4 Prediction Markets entries (polymarket_arbitrage, polymarket_copy_trading, kalshi_arbitrage, kalshi_llm_arbitrage).

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`. Web up on port 8000.
- Startup log: `Registered kalshi broker for division=kalshi_arbitrage (paper=False)` AND `Registered kalshi broker for division=kalshi_llm_arbitrage (paper=False)` — both divisions wired to the read-only KalshiBroker.
- Both KalshiBrokers connected to prod, balance=$499.00 (same Kalshi account; division separation is logical not physical).
- All 4 scanners online: Polymarket (enabled), Kalshi structural (enabled), Kalshi temporal+bucket (enabled), Kalshi LLM (**enabled=False** — Board flips when ready to incur Anthropic cost).
- Dashboard renders both `/division/kalshi_arbitrage` and `/division/kalshi_llm_arbitrage` tiles in the Prediction Markets group.

**Notable code decisions:**
- **Reuse over fork:** the strategy file is a structural clone of polymarket_arbitrage, NOT a refactor that generalizes both. Refactoring to a shared base class would be cleaner long-term but riskier in one-shot — easier to keep two parallel strategies for now and refactor when a third venue (or a fundamentally different LLM-divergence variant) lands.
- **Field-name mapping at the HTMX endpoint** (not in the partial template) keeps `polymarket_analysis.html` venue-agnostic. The endpoint constructs an `event` dict matching the polymarket field shape; template doesn't know it's rendering Kalshi data.
- **Same risk gate, no kalshi-llm-specific dispatch.** Risk verdict will fall through to default rules until we Board-flip enabled=True and see whether a $1/leg sizing + 10% divergence threshold + 6h cooldown produces useful behavior. Adjust `risk.yaml kalshi:` section caps then if needed.
- **Cost budget:** roughly doubles polymarket's daily Anthropic spend ($2-50/day → estimated $4-100/day) when enabled. Prompt caching is shared across both polymarket + kalshi_llm calls (same persona prefix), so per-call marginal cost stays low.

**Inert / dormant:**
- Strategy is `enabled: false` by default. Loop wakes every 60s and no-ops. Discovery isn't triggered until enabled=True. To start: flip `kalshi_llm_arbitrage.enabled` in `strategies.yaml` (hot-reloadable; no restart needed).
- No live order placement (Phase K7+).
- No data layer for round-trips / equity snapshots specific to this division (still K2.4 deferred — applies to both Kalshi divisions).

**Memory updates:** `trading_corp_kalshi.md` Phasing block needs K6.1 marked SHIPPED (separate edit).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k61-llm-20260511-0048; BASE=/home/azureuser/trading_corp; \
mv \$BASE/config/divisions.yaml.\$TAG \$BASE/config/divisions.yaml; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/routes.py.\$TAG \$BASE/trading_corp/web/routes.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG \$BASE/trading_corp/web/templates/division.html; \
rm \$BASE/trading_corp/agents/strategies/kalshi_llm_arbitrage.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 00:13 UTC — Kalshi Phase K2.3.1 (per-candidate audit events for true polymarket-density rail)

**Triggered by:** Board's second review of the dashboard found K2.3 still didn't match polymarket density. Root cause: aggregate scan summaries vs. polymarket's per-market rows. The polymarket rail emits `polymarket_llm_probability_called` per market evaluated (10-20 rows per cycle showing market_slug, question, LLM/market/divergence per row). Kalshi was only emitting one summary row per scan ("scanned 620 markets, 0 opps") — missing the per-market grain entirely.

**Files deployed (5 modified):**
- `trading_corp/agents/strategies/kalshi_tail_price_arb.py` — collect ALL examined tail candidates (not just ones above threshold) into a list with full context (ticker, event_title, category, subtitle, prices, edge_dollars, would_emit, expires_at). After scan, emit `kalshi_market_evaluated` audit event for top-N (default 5) sorted by edge descending. Same UX role as `polymarket_llm_probability_called` minus the LLM cost.
- `trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py` — parallel additions: emit `kalshi_pair_evaluated` per top-N temporal pair (event_title, early/late ticker + dates + yes_ask, edge_cents, would_emit) and `kalshi_bucket_evaluated` per bucket event (n_legs, sum_yes_asks, edge_cents, would_emit). New config knob `audit_top_n_candidates` (default 5).
- `trading_corp/web/data.py` — added the 3 new event kinds to the `_query_division_activity` SQL whitelist + per-kind enrichment fields (event_title, category, prices, would_emit etc.).
- `trading_corp/web/templates/division.html` — added 3 new inline rendering branches for `kalshi_market_evaluated`/`kalshi_pair_evaluated`/`kalshi_bucket_evaluated`. Each row shows ticker + tail/category badges + event title (the load-bearing human-readable text) + prices/sum/edge ratio strip + threshold. ARB-grade events get the gain color; below-threshold ones stay muted. Mirrors polymarket's market_slug → question → LLM/market/divergence layout.
- `trading_corp/web/templates/partials/kalshi_analysis.html` — added 3 new per-kind rich panels for the right-rail expansion: market_evaluated (3-card grid YES_ask/NO_ask/edge + tail badges + sum line), pair_evaluated (2-card grid early/late + constraint analysis), bucket_evaluated (3-card grid legs/sum/edge + would-emit verdict).

**Backup tag:** `pre-kalshi-k231-percandidate-20260511-0012`

**Pre-deploy verification:**
- All 4 affected Python/template files parse cleanly.
- Per-strategy `audit_top_n_candidates` knob defaults to 5; not exposed in strategies.yaml — relies on the default until tuning needed.

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`. Web server back up on port 8000.
- Both scanners online with enabled=True.
- First scan tick after restart fires at +300s; per-candidate audit events expected at +~310s. (Verification after monitor fires.)

**Notable code decisions:**
- The per-candidate emission DOES NOT change the order-emission path — opportunities above threshold still flow through the existing `_TailOpportunity` / `_TemporalOpportunity` / `_BucketOpportunity` lists into ProposedOrders. The new events are AUDIT-ONLY; they document "what we looked at and why we didn't trade" so the rail has substance even when 0 orders fire.
- Top-N sort order = edge descending. So the rail surfaces the NARROWEST MISSES first (markets closest to triggering an arb) — actionable visibility into where the strategy is most likely to fire next, vs random sampling.
- Polymarket's `polymarket_llm_probability_called` event acts as the inspiration. Same per-candidate grain. Different field shape (no LLM reasoning text, just structural pricing + edge).
- Per-pair audit for K2.2 walks the same date-sorted pairs the detector walks. Cost is O(N²) in markets per event but events are small (≤10 markets typical) so this is cheap.

**Inert / dormant:**
- Top-N is hard-coded to 5 (configurable via `audit_top_n_candidates` strategies.yaml knob). With both K2.1 + K2.2 + bucket scans firing per cycle = up to 15 audit rows per 5-min cycle. Manageable.
- Round-trips table + 5-min equity snapshots STILL not shipped — that's K2.4. Will need to land before paper trades start firing for PnL tracking.

**Memory updates:** None — `trading_corp_kalshi.md` K2.3 entry is sufficient; the rail-grain refinement is described in this deploy log only.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k231-percandidate-20260511-0012; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py.\$TAG \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py; \
mv \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py.\$TAG \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG \$BASE/trading_corp/web/templates/division.html; \
mv \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html.\$TAG \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-11 00:04 UTC — Kalshi Phase K2.3 (dashboard parity with Polymarket)

**Triggered by:** Board flagged that Kalshi Arbitrage division drill-down at `/division/kalshi_arbitrage` showed "useless" rows that didn't expand and had nothing to inspect — vs. Polymarket which has rich inline rendering + HTMX-loaded analysis panel. K2.1 + K2.2 audit events were landing in the table (post-23:55 activity panel-whitelist fix) but only as bare kind-name labels. This deploy closes that gap by mirroring the Polymarket pattern across all four dashboard surfaces (data enrichment, inline rendering, HTMX expansion endpoint, partial template).

**Files deployed (3 modified, 1 new):**
- `trading_corp/web/data.py` — `_query_division_activity` enriches each kalshi event with a `kalshi: {...}` sub-dict (parallel to `polymarket: {...}`). Per-kind extra fields:
  - `kalshi_discovery_refreshed`: `n_events_total`, `n_markets_total`, `n_markets_filtered_collection`, `events_by_type`
  - `kalshi_tail_arb_scan`: `n_markets_scanned`, `n_tail_candidates`, `n_opportunities_above_threshold`, `min_edge_cents`, `yes_max/min_for_*_tail`
  - `kalshi_temporal_bucket_scan`: `n_temporal/bucket_events_scanned`, `n_temporal/bucket_opportunities`, threshold cents
  - `would_have_placed` / `kalshi_*_order_rejected_by_risk`: `ticker`, `event_ticker`, `edge_cents`, `leg`, `kalshi_pair_id`/`kalshi_arb_set_id`, `kalshi_arb_type`, `qty`, `limit_price`, `risk_verdict`, `risk_reason`
- `trading_corp/web/templates/division.html` — added `{% elif evt.kalshi %}` branch in the recent-activity loop. Inline rendering branches per kind:
  - Scan summaries: counts inline (e.g. "scanned: 620 markets · tail candidates: 259 · opps≥1.0c: 0 · tail≤0.05/≥0.95")
  - Discovery refreshed: events/markets totals + by-type chips
  - would_have_placed / risk-rejected: ticker + leg badge + arb-type badge + edge cents (color-coded) + cost + set/pair id
- `trading_corp/web/routes.py` — new `GET /partials/kalshi-analysis/{event_id}` endpoint mirroring `partial_polymarket_analysis`. Loads audit row, validates actor is one of the kalshi strategies, formats rich event dict, hands off to template.
- `trading_corp/web/templates/partials/kalshi_analysis.html` — **NEW.** Right-rail partial returned by HTMX. Per-kind rich panels (3-card grids for scan summaries, ticker+edge+max-risk for orders), pair/set linkage, risk reason callout, collapsible raw audit payload at the bottom for full inspection.

**Backup tag:** `pre-kalshi-k23-dashboard-20260511-0004`

**Pre-deploy verification:**
- All 4 affected files parse cleanly (Python AST + Jinja syntax via FastAPI startup).
- 7 webhook test failures present BEFORE this deploy — pre-existing `_Deps.bitunix_observer` attribute issue unrelated to dashboard work. 58 division/web/polymarket tests pass.

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`.
- `curl http://localhost:8000/division/kalshi_arbitrage` returns rendered HTML with all expected markers: "tail-price scan", "temporal+bucket scan", "discovery refreshed", "tail candidates:", "opps≥1.0c:", "Show details →" buttons on every kalshi row.
- `curl http://localhost:8000/partials/kalshi-analysis/{id}` returns rich HTML for a kalshi_temporal_bucket_scan event — header strip + 3-card grid (temporal/bucket events scanned + opps vs threshold) + collapsible raw payload. Identical pattern to polymarket-analysis partial.

**Notable code decisions:**
- The `kalshi: {...}` sub-dict mirrors `polymarket: {...}` shape so future-Claude can reason about the two prediction-market venues symmetrically. Only divergence: kalshi has multiple event-kind shapes (scan, discovery, order) so the dict has more conditional fields.
- Reused the `#pair-analysis` HTMX target on the right-rail panel — both polymarket and kalshi load into the same slot. The right rail surfaces "the most-recently-clicked event's detail," regardless of venue. Acceptable given the panel is single-purpose.
- Color contract preserved: green for `would_have_placed`, red for `risk_rejected`, mono for "scan with opportunities found", muted for "scan with 0 opportunities". Edge-cent colors: green ≥5¢, mono ≥2¢, muted <2¢. Matches the polymarket divergence color ladder.
- Raw payload always available via `<details>` collapsible — escape hatch for debugging without needing to query SQLite.

**Inert / dormant:**
- The "Expert Analysis" right-rail header still says "Click any position on the left to see its expert analysis here." That copy is for the polymarket use case and reads slightly off for kalshi (which has 0 positions, so there's nothing to click). Cosmetic; can update to "Click any activity row" later. Not blocking.
- Round-trips table + 5-min equity snapshots for Kalshi still NOT shipped — those are still K2.4 deferred work (need them once `would_have_placed` rows start landing for paper-PnL tracking).
- Position panel still shows "No positions detected for this division yet" — correct because `KalshiBroker.snapshot()` returns 0 positions (we have no executed orders, only paper would_have_placed rows).

**Memory updates:** `trading_corp_kalshi.md` Phasing block updated to mark K2.3 dashboard parity SHIPPED (separate edit).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k23-dashboard-20260511-0004; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/data.py.\$TAG \$BASE/trading_corp/web/data.py; \
mv \$BASE/trading_corp/web/routes.py.\$TAG \$BASE/trading_corp/web/routes.py; \
mv \$BASE/trading_corp/web/templates/division.html.\$TAG \$BASE/trading_corp/web/templates/division.html; \
rm \$BASE/trading_corp/web/templates/partials/kalshi_analysis.html; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-10 23:43 UTC — Kalshi Phase K2.2 + K2.0 discovery rate-limit hotfix

**Triggered by:** (a) Continuation of Kalshi roadmap immediately after K2.1 ship — K2.2 temporal + bucket arb detector ready to ship, (b) **incident response**: between 23:35 and 23:42 UTC the K2.1 strategy was Board-flipped to `enabled: true` for overnight audit data collection, but the discovery code immediately hit Kalshi's rate limit hard. pykalshi's `get_all_series(category=X, limit=N)` silently fetches ALL series for the category despite the `limit` param (or defaults `fetch_all` somewhere in its pagination logic), so the discovery enumerated **4482 series across 6 categories** instead of the configured 30/category × 6 = 180. Each series then triggered a `get_markets` call, all hitting 429 from Kalshi, retrying 3× per pykalshi's internal handler. Result: ~13K HTTP requests in flight, scan never completing. No financial cost (Kalshi reads are free, no LLM in loop, all 429s are rate-limit pushback not billable failures), but a noisy log + immediate disable required.

**Files deployed (3 modified, 1 new):**
- `trading_corp/data/kalshi_market_map.py` — **CAP FIX:** `discover_by_categories` now truncates the get_all_series result to `max_series_per_category` BEFORE iterating get_markets — pykalshi's `limit` param is documented as unreliable as a true cap. Added `inter_call_delay_sec=0.15` between get_markets calls (sustained ~6.7 req/s vs. Kalshi's ~5-10 req/s rate limit). Same delay applied between per-event `get_event` calls inside `_build_discovery_result`.
- `trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py` — **NEW.** `KalshiTemporalBucketArbAgent` strategy. Two detection methods sharing one class:
  - **TEMPORAL:** for events classified `EventType.TEMPORAL`, parse subtitle dates ("Before July 2026", "Before 2027", "Before Jan 20, 2029" etc — `parse_subtitle_date` handles ISO / Quarter / Month-Day-Year / Month-Year / Year-only formats). Sort markets by date. For each pair (early, late), if `yes_ask_early - yes_ask_late ≥ min_edge_cents` (default 4¢, clears 2-leg taker fees ~2-4¢ typical), emit a 2-leg arb set: BUY NO on early + BUY YES on late. Worst-case payout = $1, profit = edge_cents.
  - **BUCKET:** for events classified `EventType.BUCKET`, sum yes_ask across all markets in event. If `1 - sum ≥ min_edge_cents` (default 5¢, clears N-leg fees), emit an N-leg arb set: BUY YES on every leg. Guaranteed payout = $1.
  - Multi-leg ProposedOrders share `kalshi_arb_set_id`, `kalshi_arb_type` (`temporal` | `bucket`), and per-set risk metadata.
- `trading_corp/main.py` — added `KalshiTemporalBucketArbAgent` instantiation + `_scheduled_kalshi_tb_arb_loop` (parallel to `_scheduled_kalshi_arb_loop`). Both kalshi loops share the same `kalshi_arbitrage` division and broker; pykalshi internal cache means duplicate discovery within ttl is cheap. Cancellation hook added in shutdown path.
- `config/strategies.yaml` — new `kalshi_temporal_bucket_arb:` block with discovery / temporal / bucket / sizing / per_cycle config, `enabled: false` default. `kalshi_tail_price_arb` flipped back to `enabled: false` as part of incident response (line annotated with disable reason).

**Backup tag:** `pre-kalshi-k22-discoveryfix-20260510-2343`

**Pre-deploy verification:**
- Local syntax check: all 4 affected Python files parse cleanly.
- Local pytest: kalshi/main test slice passes — zero regressions.

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`. The restart immediately killed the in-flight 429 retry storm.
- Startup log:
  - `Registered kalshi broker for division=kalshi_arbitrage (paper=False)`
  - `Polymarket arbitrage scanner online (enabled=True, auto_execute=False, hitl=DIRECT)`
  - `Kalshi arbitrage scanner online (enabled=False, auto_execute=False, hitl=DIRECT)` ← K2.1, temp-disabled
  - `Kalshi temporal+bucket arb scanner online (enabled=False, auto_execute=False, hitl=DIRECT)` ← K2.2, default-off
- Zero 429s in the 60-second window after restart (vs. continuous 429 storm pre-restart).
- Prod end-to-end K2.2 strategy smoke (forced `enabled: true` via temp config, single cycle, 3 categories × 15 series × 20 markets, ~45 series): **0 arb sets, 0 total legs**. Honest baseline — most TEMPORAL pairs satisfy P(early) ≤ P(late) and most BUCKET sums equal $1 exactly. Real opportunities will surface during dislocations (event windows, news shocks, illiquid hours).

**Notable code decisions:**
- Cap fix is at OUR consumption layer (`for s_obj in series: if cat_count >= max ...`) rather than relying on pykalshi to enforce — defense in depth against future SDK behavior changes.
- 150ms inter-call delay is a soft rate limit (~6.7 req/s sustained) chosen to stay comfortably under Kalshi's empirical ~5-10 req/s threshold without artificially slowing discovery. With `max_series_per_category=30 × 6 categories = 180` series + ~50 events post-grouping, total scan cost ≈ (180 + 50) × 0.15s + actual HTTP latency ≈ 60-90 seconds per cycle. Cache-ttl 600s means at most 6 cycles/hour, totally manageable.
- `parse_subtitle_date` returns the LATEST possible date interpretation ("Before July 2026" → 2026-07-31) so temporal ordering matches the semantic constraint P(by month-end) ≤ P(by later-month-end).
- TEMPORAL arb position structure (BUY NO early + BUY YES late) is correct because the arb requires capturing the constraint violation regardless of which scenario resolves. Min payout = $1; profit = `yes_ask_early - yes_ask_late` minus fees.
- BUCKET arb is structurally simpler (sum < $1 = free money) but rarer; risk lives in N-leg fee burden which is why the threshold is set higher (5¢ vs 4¢).

**Inert / dormant:**
- BOTH Kalshi strategies are `enabled: false` post-deploy. K2.1 was temp-disabled mid-incident; K2.2 default-off awaiting Board review. To start collecting overnight audit data: flip both `enabled: true` in `strategies.yaml` (hot-reloadable, no restart needed — agents re-read on every cycle).
- No data layer (round-trips table / 5-min equity snapshots) shipped here. Still K2.3, deferred. Until then, the Kalshi Arbitrage tile shows broker-level account balance only.
- Risk gate still has no kalshi-specific dispatch in `risk.py`; orders fall through to default rules. Acceptable for K2.x while strategies are off; revisit before flipping enabled to true if we want belt-and-suspenders.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k22-discoveryfix-20260510-2343; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/data/kalshi_market_map.py.\$TAG \$BASE/trading_corp/data/kalshi_market_map.py; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
rm \$BASE/trading_corp/agents/strategies/kalshi_temporal_bucket_arb.py; \
sudo systemctl restart trading-corp
"
```
*(Rollback restores the unfixed discovery code AND the pre-K2.2 main.py — do NOT re-enable kalshi_tail_price_arb after rollback; the cap fix is required to avoid re-triggering the 429 storm.)*

**Memory update:** `trading_corp_kalshi.md` Phasing block needs K2.2 marked SHIPPED (separate edit).

---

## 2026-05-10 23:28 UTC — Kalshi Phase K2.0 + K2.1 (discovery + tail-price arb scanner)

**Triggered by:** Continuation of Kalshi roadmap after K1 deploy at 22:29 UTC. Locked plan: Option C from the K2 phase-slicing conversation = K2.0 (market discovery + classification) + K2.1 (tail-price YES/NO arb detector) shipped together. Memory `trading_corp_kalshi.md` has the full phasing.

**Files deployed (4 modified, 2 new):**
- `trading_corp/data/kalshi_market_map.py` — **NEW.** Discovery + classification module. `is_tradeable_market` + `get_market_prices` lifted (MIT) from ryanfrigo/kalshi-ai-trading-bot — handles the API-v2 dollar-floats-vs-legacy-cents-int field-naming drift and the collection-ticker $1/$1 sentinel guard. EventType enum (BINARY / MULTI_OUTCOME / TEMPORAL / BUCKET / COLLECTION / OTHER). MarketRecord + EventRecord dataclasses. Two discovery paths: `discover_by_categories` (PRIMARY — category → series → markets traversal via `get_all_series`/`get_markets`) and `discover_open_markets` (DEPRECATED — bulk OPEN-markets endpoint returns ~all KXMVE* sports parlay containers in the first pages and pagination terminates inside the noise). Subtitle pattern matchers heuristically classify TEMPORAL ("before/by <date>") vs BUCKET ("Q1 2026" / month names).
- `trading_corp/agents/strategies/kalshi_tail_price_arb.py` — **NEW.** `KalshiTailPriceArbAgent` strategy mirroring the polymarket pattern (mtime-cached config from `strategies.yaml`, cooldown persistence in `agent_state` table). Per-cycle: refresh discovery cache (default ttl 600s), walk all non-COLLECTION events, find markets at YES_mid ≤5¢ or ≥95¢, check YES_ask + NO_ask < $1 - threshold (default 1¢ minimum edge), emit ProposedOrder pairs. Per-pair sizing: $1/leg fixed (paper-only). Each pair shares a `kalshi_pair_id` so audit + future replay can correlate the two legs.
- `trading_corp/brokers/kalshi.py` — added `list_markets()` method (broker-level abstraction matching PolymarketBroker pattern). Strategies don't talk to pykalshi directly — they call `broker.list_markets()` and get a `DiscoveryResult`.
- `trading_corp/main.py` — added `KalshiTailPriceArbAgent` instantiation alongside `PolymarketArbitrageAgent`. New `_scheduled_kalshi_arb_loop` (~150 LOC, mirror of `_scheduled_polymarket_arb_loop`) handles per-cycle scan + risk evaluation + audit logging + Telegram ping (per-pair, slim). Cancellation hook added in shutdown path.
- `config/strategies.yaml` — new `kalshi_tail_price_arb:` block with discovery / tail / sizing / per_cycle config, `enabled: false` default.
- `config/risk.yaml` — new `kalshi:` section with per-order, daily-aggregate, and total-open caps (intentionally tiny: $5/leg, $50/day, $50 total). Tail-specific universe params (yes_max=0.05, yes_min=0.95, min_edge_cents=1.0).

**Backup tag:** `pre-kalshi-k2-20260510-2328`

**Pre-deploy verification:**
- md5-diff (CRLF-normalized) all 4 modified files: clean — only my K2 additions, no prod-only drift.
- 2 new files confirmed absent on prod prior to push.
- Local pytest: 66 polymarket_arbitrage / risk / main / kalshi tests pass — zero regressions.
- Local discovery sanity-check (3 categories × 20 series × 20 markets) yielded 88 multi_outcome / 75 temporal / 1 bucket / 1130 tail-candidate-mids events — confirming the classifier picks up real Kalshi structure.

**Post-deploy verification (prod):**
- PID changed (clean restart). `systemctl is-active trading-corp` = `active`.
- Startup log:
  - `Registered kalshi broker for division=kalshi_arbitrage (paper=False)`
  - `Polymarket arbitrage scanner online (enabled=True, auto_execute=False, hitl=DIRECT)`
  - `Kalshi arbitrage scanner online (enabled=False, auto_execute=False, hitl=DIRECT)` ← new
- Zero warnings/errors since restart.
- Prod end-to-end strategy smoke (forced `enabled: true` via temp config, single cycle): **0 pairs / 0 legs**. Honest baseline — most Kalshi tail markets price efficiently to YES+NO=$1.00; real arb edges only appear during dislocations. Detector is working correctly.

**Notable code decisions:**
- The KEY_VAULT-backed env loader handles the new `KALSHI_API_KEY_ID` / `KALSHI_PRIVATE_KEY_PEM` already (added in K1). No secrets work needed for K2.
- `discover_open_markets` deliberately kept as a DEPRECATED audit/exploration tool. Its docstring documents WHY it's not the primary path so a future-Claude doesn't try to revive it.
- `_TailOpportunity` dataclass internal to the strategy keeps the discovery → ranking → ProposedOrder pipeline readable.
- Risk gate falls through to default rules when evaluating Kalshi orders today — `risk.yaml kalshi:` section is in place but `risk.py` doesn't yet have a `kalshi`-specific dispatch like polymarket does. Acceptable for K2.1 because (a) strategy is `enabled: false` by default, (b) $1/leg sizing won't bind any reasonable cap. Will add proper kalshi dispatch when we Board-flip enabled to true.

**Inert / dormant:**
- Strategy is `enabled: false` — discovery does not run, no orders emit. Loop wakes every `poll_interval_sec` (default 300s) and no-ops while disabled. Flip to true via `strategies.yaml` for shakedown.
- ProposedOrders go to `would_have_placed` audit rows only (paper). Live KalshiLiveBroker.place_order is Phase K5+ — gated on observed positive-EV across paper trades.
- No data layer (round-trips table / 5-min equity snapshots) shipped here. That's K2.3, deferred. Until then, the Kalshi Arbitrage tile shows broker-level account balance only.

**Memory updates:** `trading_corp_kalshi.md` Phasing block updated to mark K1 / K2.0 / K2.1 as SHIPPED with timestamps + design notes.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k2-20260510-2328; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
mv \$BASE/config/risk.yaml.\$TAG \$BASE/config/risk.yaml; \
mv \$BASE/trading_corp/brokers/kalshi.py.\$TAG \$BASE/trading_corp/brokers/kalshi.py; \
rm \$BASE/trading_corp/data/kalshi_market_map.py; \
rm \$BASE/trading_corp/agents/strategies/kalshi_tail_price_arb.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-10 22:29 UTC — Kalshi Phase K1 (read-only broker + Prediction Markets dashboard group)

**Triggered by:** Project pivot to add full Kalshi support, Polymarket copy-trading deprioritized. See memory `trading_corp_kalshi.md` for the locked phasing (K1 read-only broker → K2 intra-Kalshi temporal arb → K3 copy trading via leaderboard scraping → K4 multi-outcome arb → K5+ live orders). This deploy is K1: KalshiBroker (snapshot + quote), dashboard tile, KV-managed credentials, and a vendor-neutral "Prediction Markets" investment-type group housing Polymarket + Kalshi divisions side-by-side.

**Files deployed (4 modified, 1 new):**
- `trading_corp/brokers/kalshi.py` — **NEW.** `KalshiBroker(ReadOnlyBroker)` on top of `pykalshi.AsyncKalshiClient`. `snapshot()` fetches `portfolio.get_balance()` (cents → dollars, returns equity=cash+portfolio_value, buying_power=cash) plus `get_positions()`. `quote(ticker)` returns mid from `market.get_orderbook()`. RSA private key PEM materialized to a restricted-perms `/tmp/kalshi_*.pem` tempfile at connect, deleted on disconnect (pykalshi requires a filesystem path for the key). Stub mode if either credential is missing — tile renders "online · $0" rather than "not_wired", same pattern as BitUnix/Polymarket bring-up.
- `trading_corp/utils/secrets.py` — added `kalshi_api_key_id` + `kalshi_private_key_pem` fields, KV expected-vars list (KALSHI-API-KEY-ID, KALSHI-PRIVATE-KEY-PEM), `register_redact_literal(kalshi_private_key_pem)` so the PEM never lands in logs even if a third-party lib echoes it.
- `trading_corp/main.py` — added `if family == "kalshi"` broker-factory branch mirroring the polymarket pattern (no PaperExecutionBroker wrap — read-only adapters don't need it). Demo-mode toggle via `KALSHI_USE_DEMO=1` env var (defaults off — production / kalshi.com).
- `config/divisions.yaml` — added `kalshi_arbitrage` placeholder division (broker=kalshi, standby=true, intent=aggressive). Phase K2 wires the actual temporal/bucket arb scanner against this division.
- `trading_corp/utils/divisions.py` — **renamed group key `polymarket` → `prediction_markets`**, label "Polymarket" → "Prediction Markets". Routing extended via `_PREDICTION_MARKET_BROKERS = {"polymarket","kalshi"}` and `_PREDICTION_MARKET_SLUG_PREFIXES = ("polymarket_","kalshi_")` so both venues' divisions land in the new group regardless of broker family.
- `requirements.txt` — added `pykalshi>=1.0.6` (MIT, async + sync, RSA-PSS auth handled, REST + WS).

**Backup tag:** `pre-kalshi-k1-20260510-2229`

**Pre-deploy verification:**
- md5-diff prod vs local (CRLF-normalized) for the 4 modified files: clean — only my additions, no prod-only drift.
- Local pytest: 17 division/secrets/broker tests pass, zero regressions.
- Local smoke against real Kalshi prod account: `$499.00` cash, 0 positions, balance + positions endpoints return HTTP 200.

**Secrets uploaded to Azure Key Vault (kv-tc-vtwbowt3wtkpy):**
- `KALSHI-API-KEY-ID` (UUID)
- `KALSHI-PRIVATE-KEY-PEM` (1674 chars, multi-line PEM byte-perfect via `az keyvault secret set --file`)
- Read-back verified both values match local `.env`.

**Post-deploy verification (prod):**
- PID 199160 → 200767 (clean restart). `systemctl is-active trading-corp` = `active`.
- Startup log: KV fetched both KALSHI secrets, "Registered kalshi broker for division=kalshi_arbitrage (paper=False)" landed.
- First dashboard hit triggered `KalshiBroker.connect()` lazily (PolymarketBroker pattern); log shows "KalshiBroker connected (prod) — balance=$499.00 portfolio=$0.00".
- Dashboard root (`http://localhost:8000/`) renders the **"Prediction Markets"** group header containing the Kalshi Arbitrage tile with `equity = $499.00`, badges `aggressive` + `standby`. Polymarket Arbitrage + Polymarket Copy Trading also render in the same group (group rename was transparent).
- Zero warnings/errors since restart.

**Notable code decisions:**
- `KalshiBroker` subclasses `ReadOnlyBroker` (NOT `Broker`) — there is no `place_order` method on the type, so a code path that tries to place orders against Kalshi is a static type error, not a runtime exception. Same isolation guarantee as PolymarketBroker. Live order placement (Phase K5+) will land as a separate `KalshiLiveBroker(Broker)` when greenlit.
- pykalshi takes a filesystem path to the PEM, not bytes. The materialize-to-tempfile-on-connect pattern keeps the PEM out of the repo and out of any committed file; tempfile is deleted on `disconnect()` and restricted to owner-rw on POSIX.
- Group rename `polymarket → prediction_markets` was scoped to `utils/divisions.py` only — no template / data-layer references to the group key existed elsewhere in the codebase (the `evt.polymarket` references in `web/data.py` and templates are about polymarket-event analysis, not the group key).

**Inert / dormant:**
- `kalshi_arbitrage` division is `standby:true` — broker reads $499 balance and 0 positions, but no strategy operates on it yet. Phase K2 wires the temporal/bucket arb scanner.
- `place_order` / `cancel_order` not present on KalshiBroker by design (ReadOnlyBroker base). Phase K5+ will introduce KalshiLiveBroker.
- Volume Incentive Program ($0.005/contract cashback on trades 3¢-97¢) is a pending verification item — need to confirm per-side vs per-round-trip + qualification gates before Phase K2 sizing math relies on it.

**Memory updates:** `trading_corp_kalshi.md` already locked the architecture pre-deploy (SDK choice, repo-pillaging shortlist, phasing). No memory edit needed for this entry.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-kalshi-k1-20260510-2229; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/utils/secrets.py.\$TAG \$BASE/trading_corp/utils/secrets.py; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/config/divisions.yaml.\$TAG \$BASE/config/divisions.yaml; \
mv \$BASE/trading_corp/utils/divisions.py.\$TAG \$BASE/trading_corp/utils/divisions.py; \
rm \$BASE/trading_corp/brokers/kalshi.py; \
sudo systemctl restart trading-corp
"
```
*(Rollback restores the "Polymarket" group label and removes the Kalshi tile. Does NOT remove the KV secrets — they stay as orphans, harmless. pykalshi stays pip-installed in the venv, also harmless.)*

---

## 2026-05-10 21:01 UTC — Pink Box signal-name cleanup (dead-code purge)

**Triggered by:** Board re-confirmed end of session that Pink Box is NOT a TradingView alert — it's a static S/R image refreshed 2-3×/day. Today's audit log showed `pink_box_bear` firing 4× in a 9-min window this morning (08:48-09:00 UTC, BTCUSD/3) on what is most likely an old Coinbase TV alert from prior setup. Cleanup directive: remove every code path that treats `pink_box_bull/bear` as a valid arming signal, so any future stray webhook becomes `unknown_signal` (silent reject, audit row, no agent action).

**Files deployed (3):**
- `trading_corp/agents/strategies/lord_otter.py` — removed `pink_box_bull/bear` from `KNOWN_SIGNALS`, `_BULL_SIGNALS`, `_BEAR_SIGNALS`. Simplified `ArmedState` (source: `"spoon"` only — was `"pink_box" | "spoon"`). Simplified the arming branch in `_refresh_state_from_signal` to a clean `if signal == "spoon_bull"` / `elif signal == "spoon_bear"` (was a dual-membership check with awkward source-string assembly).
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — removed `pink_box_bull/bear` from `OTTER_TRIGGER_BULL` / `OTTER_TRIGGER_BEAR`.
- `config/strategies.yaml` — removed `pink_box_bull/bear` weight entries; updated `spoon_bull` comment ("divergence arming"; the prior "replaces pink_box per vision" hint is moot).

**Backup tag:** `pre-pink-box-cleanup-20260510-2059`

**Pre-deploy verification:**
- md5-diff against prod showed clean diffs — only the pink_box-related lines differ (no prod-only drift to preserve).
- Local pytest: 27/27 affected tests pass; 62/62 broader lord_otter+bitunix slice passes.

**Post-deploy verification:**
- PID 196773 → 199154 (clean restart). `systemctl is-active trading-corp` = `active`.
- Startup log clean (`bitunix_futures` broker registered; BitUnix KV secrets fetched). Zero warnings/errors since restart.
- Forward behavior: any incoming webhook with `signal=pink_box_bull` or `signal=pink_box_bear` will now hit the `KNOWN_SIGNALS` validator and be rejected as `unknown_signal` rather than setting `armed_long/short` state. Strictly safer than the prior behavior.

**Inert / dormant:**
- The 10 historical `pink_box_bear` audit rows from 08:48-09:00 UTC remain in `audit_event` — append-only, no cleanup. They'll naturally fall off the recency window over time.
- Dev-only files (`tests/test_lord_otter_bias_persistence.py`, `tests/test_signal_replay.py`, `scripts/test_lord_otter_webhook.py`, `scripts/sweep_btc_accumulator.py`) were also updated locally but are NOT deployed to prod.

**BACKLOG / memory updates:** P3 entry "Pink Box S/R confluence integration" updated — code-cleanup item struck (now DONE); integration design preserved for when we want to wire static S/R levels into the bitunix tier classifier. Memory `trading_corp_otter_tuned_for_3m.md` updated to reflect deployed cleanup status.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-pink-box-cleanup-20260510-2059; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/lord_otter.py.\$TAG \$BASE/trading_corp/agents/strategies/lord_otter.py; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp
"
```
*(Rollback only if a real Pink Box TV alert turns out to exist and we want it accepted again — Board would need to assert that explicitly.)*

---

## 2026-05-10 16:56 UTC — Polymarket prompt cache fix + category-priors expansion

**Triggered by:** Board cost-optimization review of polymarket_arbitrage LLM spend ($2-50/day per deploy log). Direct prod verification (`/tmp/verify_polymarket_cache.py`) confirmed prompt caching was SILENTLY DEAD on Sonnet 4.6 — `cache_creation_input_tokens=0` and `cache_read_input_tokens=0` on every call since polymarket went live (2026-05-10 02:05 UTC). Root cause: the existing system prompt was 1,427 tokens, below Sonnet 4.6's 2,048-token minimum cacheable prefix. The `cache_control: ephemeral` marker on `_polymarket_prompts.py:ANALYST_SYSTEM_PROMPT` was no-op'd by the API. Fix: expand the system prompt past the threshold with content that's strategically valuable (category-specific priors), not filler.

**Files deployed (1):**
- `trading_corp/agents/strategies/_polymarket_prompts.py` — prompt expanded from ~1,427 → 2,513 tokens. Three changes: (1) new sports-underdog rejection worked example using the actual losing trade pattern from prod data (`mlb-nym-ari` 5¢ underdog at 90% LLM divergence — our first resolved-loss case), (2) new "Category-specific base rates and priors" section covering sports / geopolitical / Eurovision / crypto-action markets, (3) hard divergence sanity check rule (|prob_yes - implied| > 0.50 forces self-check; sports specifically capped at 0.30). Docstring updated to document the ≥2,048 token Sonnet 4.6 minimum.

**Strategic content added — these are domain priors the model otherwise lacks:**
- **Sports:** bookmaker-line markets are ~efficient → anchor within ±10pp of implied; deep underdog YES bets (<0.10 implied) are not edge opportunities; sub-markets (toss/total/first-set) priced at fair physical odds; tennis ranking-gap heuristic; MLB home-team base rate
- **Geopolitical:** short-window event markets default to <20% base rate; Iran/Middle-East markets are insider-priced (anchor near implied); war-end markets systematically over-predict
- **Eurovision:** top-5 most-bet account for ~70% of resolved-correct mass; <3% implied countries effectively never win
- **Crypto/company-action:** time-since-last-event > news headlines; tweet-count markets are Poisson; price-target markets follow options-implied vol

**Backup tag:** `pre-polymarket-prompt-cache-fix-20260510-1656`

**Verification — direct prod cache test BEFORE restart:**
```
Call 1 (cache write): input_tokens=80   cache_creation_input_tokens=2513   cache_read_input_tokens=0
Call 2 (cache read):  input_tokens=3    cache_creation_input_tokens=77     cache_read_input_tokens=2513
```
Cache is active. ~2,513 system-prompt tokens served from cache at 90% discount on every call after the first in a 5-min window. PID 194680 -> 196773 (clean restart). Service `active`. No errors in startup log.

**Cost analysis:**
- **Before (broken cache):** ~$0.0091/call (1,827 input × $3/M + 250 output × $15/M); $2-50/day depending on K-cycle activity
- **After (cache active):** ~$0.0035-$0.0044/call (2,513 cached × $0.30/M + ~150 fresh + 165 output × $15/M); estimated $0.80-$20/day
- **Savings: ~2.5× per call.** Not as dramatic as a Haiku switch (~10×) but preserves Sonnet 4.6's capability — the load-bearing assumption being that Sonnet's reasoning IS worth paying for if the prompt gives it the priors it lacks.

**Why Sonnet over Haiku:**
- Polymarket has many categories beyond sports — Sonnet may genuinely be better on politics/economics/long-tail
- The added priors directly address the empirical sports-underdog failure mode (the only clear LLM hallucination we'd resolved as of deploy time)
- Cost delta vs Haiku is ~$1/day; flip-to-Haiku remains an option if Phase 2.5 Backtester data shows Sonnet not earning its keep
- Haiku's minimum cacheable prefix is 4,096 tokens — would require ~doubling the prompt again, with diminishing returns on prior content quality

**Inert / dormant:**
- The 5-min ephemeral TTL is correct for our 30s scan cadence — every cycle's first call hydrates, the K-1 follow-ups (parallel via warm-and-fan) all hit cache
- The new sports-underdog rejection example uses real prod-loss data; if a future-Claude reads this and is tempted to fictionalize the example, leave it — citing real losses teaches discipline more effectively than synthetic ones

**Memory updates:** none required. The polymarket vision memory already references prompt-caching strategy generically; the model-specific cache-minimum table belongs in code/docs, not memory.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-prompt-cache-fix-20260510-1656; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/agents/strategies/_polymarket_prompts.py.\$TAG \$BASE/trading_corp/agents/strategies/_polymarket_prompts.py; \
sudo systemctl restart trading-corp
"
```
*(Note: rollback returns to the broken-cache state. Don't roll back unless the new priors cause a measurable regression in win rate.)*

---

## 2026-05-10 16:12 UTC — Phase 3.2a venue correction: Coinbase 5m -> BitUnix native 3m

**Triggered by:** Board flagged that the Phase 3.2a deploy at 15:33 UTC had incorrectly pointed the live bar cache at Coinbase (and downgraded to 5m due to Coinbase's lack of 3m support). The historical EDA + tier thresholds + validated divergence list were all calibrated on BTCUSDT.P (Bybit-sourced TV exports → BitUnix execution venue). Cross-venue volatility-profile drift is the exact thing this would introduce. Fix: live bar source must be BitUnix.

**Decision tree:**
- **Bybit** would match the historical EDA — but Bybit is geo-blocked from US IPs by Cloudfront. Both my local and the prod Azure VM hit 403. Not viable as a live feed.
- **BitUnix** is what we trade on — there's no cross-venue gap if data and execution share venue. BitUnix's public REST kline endpoint (`/api/v1/futures/market/kline`) works without auth, supports native 3m, and returned 60 bars cleanly when tested from prod.
- **Coinbase** (the wrong choice in 15:33 deploy) — only supports {1m, 5m, 15m, 1h, 6h, 1d}. No 3m. Different venue from execution. Keeping as fallback in code only.

**Files changed (2):**
- `trading_corp/data/live_bar_cache.py` — refactored `refresh()` to dispatch on venue. New `_refresh_bitunix()` method uses `httpx` to call BitUnix REST kline directly (no CCXT — BitUnix isn't in CCXT). New `_refresh_ccxt()` retains Bybit/Coinbase support as fallbacks. Updated module docstring with the venue selection rationale.
- `trading_corp/main.py` — `LiveBarCache(symbol="BTCUSDT", timeframe="3m", venue="bitunix", max_bars=60)`. Was: `symbol="BTC/USD", timeframe="5m", venue="coinbase"`.

**Backup tag:** none — rolled directly over the 15:33 deploy. Restart was clean.

**Verification:**
- 60/60 tests still pass (cache tests use direct `bars=` injection, no network — unaffected by venue refactor).
- PID 193755 → 194694 (clean restart).
- Bar cache primed live: `{'symbol': 'BTCUSDT', 'timeframe': '3m', 'venue': 'bitunix', 'bars_cached': 60, 'last_close': 81474.9, 'atr_14': 77.34}`. ATR is 0.095% of price — proper 3m volatility profile (5m had been 0.115%).
- No refresh errors. Poll loop online with 60s cadence.

**What changes:**
- Stops now use real 3m volatility from the same exchange we trade on. ATR-driven sizing aligns with the historical-EDA-calibrated thresholds.
- Floor (0.3%) still wins on calm bars (since 1.5×$77 = $116 << 0.3%×$81k = $244), but during news/breakout windows real ATR will exceed floor and dominate as designed.

---

## 2026-05-10 15:33 UTC — BitUnix Phase 3.2a (live OHLCV bar cache + real ATR + paper_trade_record writes)

**Triggered by:** Phase 3.2a per memory `trading_corp_bitunix_phase3_confluence_model`. Foundation work for the eventual scale-out strategy (Phase 3.2b): replaces the 0.04%-of-price ATR placeholder with real ATR(14) from a live OHLCV cache, AND fixes a critical Phase 3.1 gap — bitunix paper trades weren't writing to `paper_trade_record`, so they had no win/loss resolution path. Both fixes here.

**Files deployed (3):**
- `trading_corp/data/live_bar_cache.py` — NEW. `LiveBarCache` polls Coinbase 3m... actually 5m (see hot-fix note) OHLCV via CCXT every 60s, caches last ~60 bars in-process. `get_atr(period=14)` computes ATR using Wilder's smoothing. `run_poll_loop` is the periodic background task. Drops in-progress (partial) latest bar. Errors logged + swallowed; cache keeps serving last successful snapshot.
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — UPDATED. Constructor takes optional `bar_cache`. `_build_proposal` accepts `atr_3m` parameter; uses real ATR when supplied (with `atr_source="live_atr_14"` marker), falls back to estimate when None (`atr_source="estimate_0.04pct"`). After `would_have_placed`, writes a `paper_trade_record` row via `PaperTradeRecord.from_order` so the existing strategy-agnostic `paper_trade_replay` loop resolves it. Order's `extra` keys harmonized (`take_profit_price`, `entry_reference_price`, `source_signal`, `max_dollar_risk`, `expected_gain_if_tp_hit`, `tp_r_multiple`) so `from_order` populates all PaperTradeRecord fields cleanly.
- `trading_corp/main.py` — constructs `LiveBarCache` alongside the observer; passes it as `bar_cache=` constructor kwarg; primes the cache synchronously before background loop starts; `bitunix_bar_task = asyncio.create_task(...)` runs the poll loop alongside donchian/polymarket/replay loops. Drift-aware deploy (pulled prod's main.py and patched the additions onto it).

**Hot-fix during deploy:** Initial `timeframe="3m"` failed with Coinbase CCXT (granularity not supported — Coinbase Advanced Trade only exposes {1m, 5m, 15m, 1h, 6h, 1d}). Switched to `timeframe="5m"` as the closest supported value. ATR profile is in the same ballpark; slightly more conservative stops. **Phase 4 will likely switch to Bybit native 3m** to match the historical EDA data we ingested for the EDA scripts (`scripts/eda_btc_scalping_signals.py` etc.).

**Backup tag:** `pre-bitunix-phase3-2a-20260510-1533`

**Verification:**
- 60/60 tests pass: 8 in `tests/test_live_bar_cache.py` (ATR computation correctness w/ Wilder's smoothing, gap-open TR handling, decay after volatile period, status snapshot, timeframe parsing) + 52 in `tests/test_bitunix_futures_observer.py` (full Phase 3.0/3.1/3.2a coverage including new tests for real-ATR-driven stops, atr_3m fallback, paper_trade_record write, bar_cache error swallowing).
- PID 192018 → 193147 → 193755 (one extra restart for the 5m hot-fix). Service `active`.
- **Bar cache primed live on prod:** `{'symbol': 'BTC/USD', 'timeframe': '5m', 'venue': 'coinbase', 'bars_cached': 59, 'last_close': 81332.13, 'atr_14': 93.84}` — ATR is 0.115% of price (5m typical volatility). Below the 0.3% stop floor, so floor still wins on calm bars; on volatile bars (ATR exceeds 200), real ATR will dominate stop sizing as designed.
- **Synthetic E2E with real ATR:** seeded bias (4h bull + 1D bull) + CVD (bull) + fired Otter `spoon_bull` trigger at $81,332 → observer classified PREMIUM, built order with stop $81,088 (-0.30% floor), TP $81,820 (+0.60% = 2R), wrote paper_trade_record with order_id `e8ad588f-...` showing all fields populated (tier, source_signal, entry_reference_price, stop_price, tp_price, tp_r_multiple). `result IS NULL` so the existing replay loop will pick it up next tick.
- Synthetic test data cleaned (audit_event, proposed_order, paper_trade_record, all 3 observer state tables).

**What changes for the user:**
- BitUnix paper trades now have real ATR-based stops instead of always defaulting to the 0.3% floor (will matter once 5m volatility exceeds 0.2% — happens during news/breakout windows).
- Paper trades will RESOLVE to win/loss via `paper_trade_replay` (next run within 15 min after each placement). Audit log + dashboard will show actual outcomes, not just "would have placed."
- This unlocks the "weeks of tuning" data collection cycle the board mentioned — we can now measure paper-mode win rates by tier and decide when to flip to live.

**Inert / dormant:**
- All real-money paths unchanged. BitUnix remains paper-only. Cache failures degrade gracefully (observer falls back to ATR estimate).
- 5m bars are a proxy for 3m at the venue level. Within-tier bar volatility is in the same ballpark; the real-ATR vs floor-wins decision will rarely flip due to this.

**Memory updates:** `trading_corp_bitunix_phase3_confluence_model.md` — already documents Phase 3.2a (added in this session); now reflects deployed state.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase3-2a-20260510-1533; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
rm -f \$BASE/trading_corp/data/live_bar_cache.py; \
sudo systemctl restart trading-corp
"
```
*Returns to Phase 3.1 (no live bar cache, ATR placeholder, no paper_trade_record writes for bitunix).*

---

## 2026-05-10 15:00 UTC — BitUnix Phase 3.1 (full ladder + order proposer + paper-mode auto-execute)

**Triggered by:** Phase 3.1 of the BitUnix vision per memory `trading_corp_bitunix_phase3_confluence_model`. Builds on Phase 3.0 (bias-only observer shipped 14:19 UTC same day) by adding the volume confluence axis, full tier ladder (PREMIUM/STANDARD/WEAK/COUNTER/SKIP), order proposer, risk caps, and paper-mode auto-execute (no per-trade HITL — Board approves the GUARDRAILS once; orders flow autonomously inside them).

**Files deployed (4):**
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — REWRITTEN. Class name preserved (avoid import churn), but functionally now a full division agent. Adds: CVD direction state machine (30 min decay), full PREMIUM/STANDARD/WEAK/COUNTER/SKIP classifier, structural stop calculator (`max(1.5×ATR, 0.3%×price)`), R:R gate (≥1.5), effective-risk cap downsizing (≤0.5% per trade), daily-risk kill-switch (≤3% per UTC day), multi-leg-ready `tp_plan` schema (single leg today; ready for Phase 3.2 scale-out), risk-gate submission, paper placement via data_exec, Telegram notification on placement, and a new `bitunix_decided` audit-event kind that fires for EVERY signal regardless of trade outcome.
- `trading_corp/web/webhooks.py` — switched both background tasks (`_process_lord_otter_alert`, `_process_market_cypher_alert`) from sync `observer.observe_alert(...)` to `await observer.observe_and_decide(...)`. Still wrapped in try/except — observer cannot disrupt existing real-money paths.
- `trading_corp/main.py` — observer construction now passes `risk_agent`, `data_exec`, `logger_agent`. Telegram channel attached after channel construction (deferred wire-up). Drift detected vs HEAD; pulled prod's main.py and applied the 2 deltas onto it before redeploying.
- `config/strategies.yaml` — added `bitunix_futures` strategy block documenting the board-approved caps (effective-risk 0.5%, daily kill 3%, tier sizing PREMIUM 4%/8x, STANDARD 2%/5x, WEAK 1%/2x, COUNTER 0.5%/2x default OFF, R:R ≥ 1.5, TP at 2R, decay windows). Values today live in code constants too; YAML lift-and-shift is a future refinement.

**New tables created at startup:**
- `bitunix_observer_cvd` — one row per side ('bull'/'bear') tracking the most recent same-side CVD-flip event. Decay: 30 min.
- `bitunix_observer_daily_risk` — one row per UTC date tracking cumulative effective-at-risk % across all bitunix_futures orders that day. Halt when >= 3%.

**Tier ladder (Phase 3.1):**
- **PREMIUM** — CVD agrees + 4h agrees + 1D agrees → 4% size × 8x leverage
- **STANDARD** — CVD agrees + 4h agrees + 1D neutral → 2% × 5x
- **WEAK** — CVD doesn't agree + 4h+1D agree → 1% × 2x
- **COUNTER** — CVD agrees + HTF contradicts → 0.5% × 2x; default OFF (`counter_tier_enabled=False`)
- **SKIP** — anything else (no order)

Effective-risk cap then downsizes any tier whose `target_size × leverage × stop_distance` would exceed 0.5% account equity. R:R gate refuses any trade where TP/SL ratio < 1.5.

**Ops model:**
- `auto_execute: true` — no per-trade HITL. Risk caps + daily kill ARE the gate. Board approves these once.
- Telegram notification on every paper placement (not approval).
- Every signal logs `bitunix_decided` audit row with outcome: `placed | skipped_tier | skipped_no_deps | skipped_no_broker | skipped_no_equity | skipped_sizing | skipped_daily_kill | rejected_risk | error_*`.
- When this flips PAPER → LIVE in Phase 4, the only addition is real `BitunixBroker.place_order()` w/ leverage + isolated margin. Same caps apply.

**Backup tag:** `pre-bitunix-phase3-1-20260510-1500`

**Verification:**
- 46/46 tests pass (`tests/test_bitunix_futures_observer.py`) — full tier matrix (12 default + 4 counter-enabled), bias state w/ decay, CVD state w/ decay, order proposer math (sizing + stop + TP + R:R + effective-risk cap), daily-risk accumulation + isolation, async observe_and_decide flow w/ mocked deps (PREMIUM places order, SKIP doesn't, daily kill blocks, risk reject path).
- PID 190918 -> 192018 (clean restart). Service `active`. All 3 observer tables auto-created at startup.
- **Synthetic E2E on prod:** seeded bias (4h bull + 1D bull) + CVD (bull) + fired Otter `spoon_bull` trigger → observer correctly:
  - classified PREMIUM
  - submitted to risk gate (approved)
  - logged `would_have_placed` with order_id `f7bb0165-...`, qty 0.0198 BTC at $80,800 entry, 4% × 8x = $1,600 notional, structural stop at 0.3% floor
  - logged `bitunix_decided` outcome=placed
  - daily-risk counter incremented (cleaned post-test)
  - no Telegram (test fixture explicitly skipped to avoid spamming Board)
- Synthetic test data cleaned post-run (audit_event, proposed_order, all 3 observer tables wiped).

**Inert / dormant — what could go wrong now is bounded:**
- bitunix_futures broker is registered as `paper-exec` (verified in startup log). All "would have placed" orders simulate via PaperExecutionBroker — no real BitUnix orders issued.
- COUNTER tier defaulted OFF — no fade-the-trend trades unless explicitly enabled.
- Daily-risk kill caps the worst-case daily exposure at 3% account equity (cumulative pre-trade risk).
- Effective-risk-per-trade caps each individual trade at 0.5%.
- All deps required for order placement (`risk_agent`, `data_exec`, `logger_agent`, `bitunix_futures` broker) — observer skips with audit row if any is missing.

**What changes for the user:**
- Will start receiving Telegram pings for paper-mode BitUnix placements as Otter alerts fire and align with HTF bias + CVD.
- Frequency: bounded by Otter trigger rate (~7-15/day in Phase 3.0 observation period) further filtered by tier requirements (most will be SKIP).
- Every classification is in `audit_event` (kind `bitunix_observer_classified`) and every decision in `bitunix_decided` — visible in the activity rail / queryable in SQL.

**Memory updates:**
- `trading_corp_bitunix_phase3_confluence_model.md` — already contains the Phase 3.1 design (added in this session); now reflects the deployed state.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase3-1-20260510-1500; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/web/webhooks.py.\$TAG \$BASE/trading_corp/web/webhooks.py; \
mv \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py.\$TAG \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
mv \$BASE/config/strategies.yaml.\$TAG \$BASE/config/strategies.yaml; \
sudo systemctl restart trading-corp
"
```
*Rollback returns to Phase 3.0 (bias-only observer, no orders). New tables (`bitunix_observer_cvd`, `bitunix_observer_daily_risk`) stay in DB but are unused — drop manually if you want a clean slate.*

---

## 2026-05-10 14:19 UTC — BitUnix Phase 3.0 observer (bias-only tier classifier, no orders)

**Triggered by:** Phase 3 of the BitUnix vision per memory `trading_corp_bitunix_phase3_confluence_model`. Phase 3.0 ("observer mode") shipped first as a de-risking step before Phase 3.1 (full tier ladder w/ volume axis + ProposedOrder emission) and Phase 4 (real BitUnix order placement).

**Files deployed (4):**
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — NEW. The Phase 3.0 module. Receives Otter+Cypher webhook signals (additive, runs alongside the existing Otter/Cypher agents). Maintains a persistent bias state machine on 4h+1D timeframes fed by Cypher divergence/cross signals. For each Otter trigger on 3m, classifies into a bias-only tier (STRONG_HTF / MODERATE_HTF / COUNTER_HTF / NEUTRAL_HTF / SKIP) and writes one `audit_event` row with `kind=bitunix_observer_classified`. Emits NO ProposedOrders. Risk gate not invoked. Observer cannot affect real-money paths — every public method wraps in try/except and swallows.
- `trading_corp/web/app.py` — added `bitunix_observer: Any = None` field to `WebDeps` dataclass.
- `trading_corp/web/webhooks.py` — added one observer call at the top of each background task (`_process_lord_otter_alert` and `_process_market_cypher_alert`), wrapped in try/except. Observer runs FIRST so it captures every signal even if downstream paths crash.
- `trading_corp/main.py` — constructed the observer at startup; added `bitunix_observer` parameter to `_start_web_server`; passed it into the `WebDeps` constructor.

**New table created at startup (auto via `BitunixFuturesObserver.__init__`):**
- `bitunix_observer_bias` — one row per (timeframe, side) tracking the most recent same-side bias-setter event timestamp. Decay applied at lookup time. Schema in `bitunix_futures_observer.py:OBSERVER_BIAS_TABLE_DDL`.

**Tier ladder (bias-only — Phase 3.1 will add volume axis):**
- **STRONG_HTF** — 4h + 1D both agree with trigger
- **MODERATE_HTF** — 4h agrees, 1D neutral
- **COUNTER_HTF** — 4h or 1D contradicts (don't fade trend)
- **NEUTRAL_HTF** — both HTFs neutral (cold start)
- **SKIP** — symbol not whitelisted or signal not classifiable

**Bias decay windows:** 4h = 24h half-life; 1D = 7-day half-life. Same-direction signals refresh.

**Bias-setters (Cypher 4h + 1D):** `mc_a_longema`, `mc_a_bluetriangle`, `mc_b_gold_buy`, `mc_b_buy_circle_div`, `mc_b_buy_circle` (bull); `mc_a_blood_diamond`, `mc_a_red_diamond`, `mc_a_redx`, `mc_a_yellow_x`, `mc_b_sell_circle_div`, `mc_b_sell_circle` (bear). Dot signals excluded as too low-conviction.

**Triggers (Otter 3m):** `otter_buy/sell`, `spoon_bull/bear`, `pink_box_bull/bear`, `water_buy_small/large`, `water_sell_small/large`, `money_bag_bottom/top`. CVD flips intentionally held back — they're volume-axis input for Phase 3.1, not entry triggers.

**Symbol whitelist:** BTC only (BTC/USD, BTCUSD, BTCUSDT, BTCUSDT.P).

**Out of scope (deferred to later phases):**
- Volume confluence axis (Phase 3.1 — uses CVD-flip webhook signals + live OHLCV polled from existing Coinbase/BitUnix broker connections; no new infrastructure)
- ProposedOrder emission with structural stop, effective-risk cap, daily-loss kill, ATR-tied pullback (Phase 3.1)
- Real `BitunixBroker.place_order()` w/ leverage + isolated margin (Phase 4)
- YAML cleanup of Otter+Cypher entries from `coinbase_spot` (Phase 3.1, alongside bitunix_futures division YAML entry + broker registration)

**Backup tag:** `pre-bitunix-phase3-observer-20260510-1419`

**Verification:**
- 24/24 unit tests pass (`tests/test_bitunix_futures_observer.py`) — tier classifier matrix (12 cases), bias state machine with decay, refresh-on-same-side, opposite-side-takes-most-recent, exception-swallowing, audit-event emission.
- Drift check before deploy: `main.py` had drift between local HEAD and prod (per memory `trading_corp_prod_git_drift`). Pulled prod's content, applied my 4 additive edits onto it, scp'd back. `webhooks.py` and `app.py` had no drift.
- PID 186736 -> 190918 (clean restart). Service `active`. Observer table auto-created at startup (verified `.schema bitunix_observer_bias`).
- **Synthetic E2E test on prod:** seeded bias with a Cypher 4h `mc_b_buy_circle_div` (bull), then fired an Otter 3m `spoon_bull` trigger — observer correctly classified MODERATE_HTF (4h=bull, 1D=neutral). Synthetic test data cleaned from `audit_event` and `bitunix_observer_bias` post-test so the audit trail isn't polluted.
- Awaiting first real signal: next natural Otter alert will write the first real `bitunix_observer_classified` audit row.

**Inert / dormant:**
- Observer runs purely for telemetry. No orders, no risk-gate participation, no broker interaction. Failure modes are bounded to "no audit row written" — never "wrong order placed" or "real money lost."
- Bias state will start populating as Cypher signals arrive. Cypher webhooks ARE active (the strategy agent is `enabled: false`, but the webhook handler always runs the background processor, which now ALWAYS calls the observer first).

**Memory updates:**
- `trading_corp_bitunix_phase3_confluence_model.md` — already contains the full Phase 3.0 design (added in earlier session); now reflects the deployed state.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase3-observer-20260510-1419; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/web/webhooks.py.\$TAG \$BASE/trading_corp/web/webhooks.py; \
mv \$BASE/trading_corp/web/app.py.\$TAG \$BASE/trading_corp/web/app.py; \
rm -f \$BASE/trading_corp/agents/divisions/bitunix_futures_observer.py; \
sudo systemctl restart trading-corp
"
```
*(Note: rollback removes the new file but leaves `bitunix_observer_bias` table + any `bitunix_futures_observer` audit rows in the DB — drop manually if you want a clean slate.)*

---

## 2026-05-10 04:19 UTC — BitUnix Futures equity 2× double-count fix

**Triggered by:** Board flagged the dashboard tile + division-detail page rendering BitUnix equity at exactly 2× the cash balance ($6,763.94 vs real $3,381.97). BACKLOG #32 P2. Fixed during a quiet pass while polymarket accumulates trades for the Phase 2.5 Backtester gate.

**Files deployed (1):**
- `trading_corp/brokers/bitunix.py` — `coin_equity` formula now sums `available + frozen + margin + crossUnrealizedPNL + isolationUnrealizedPNL` (dropped `transfer` AND `bonus`). Comment block at lines ~180-205 rewritten with corrected field semantics + the empirical reconciliation that drove the fix.

**Root cause:**
Live `/api/v1/futures/account` data showed `transfer` and `bonus` are *attribution metadata* — they describe the share of the current `available` balance that arrived via wallet-transfer (`transfer`) or promo credit (`bonus`). They are ALREADY counted inside `available`, not separate buckets. The 2026-05-03 deploy comment that called transfer "additive" was wrong (one-shot reconciliation that didn't generalize). Per-coin observation:

| Coin | available | transfer | bonus | OLD coin_equity | NEW coin_equity |
|---|---|---|---|---|---|
| USDT | 25.27 | 0 | 25.27 (dup) | 50.55 | 25.27 |
| USDC | 3356.70 | 3356.70 (dup) | 0 | 6713.39 | 3356.70 |
| **Total** | | | | **$6,763.94** | **$3,381.97** |

Note: BACKLOG #32 hypothesis flagged `transfer` only; `bonus` duplication was discovered during verification. BitUnix shows whichever attribution applies (transfer vs promo) — could be either field for any given coin. Both must be excluded.

**Verification step (one-off, kept as a script):**
- `scripts/verify_bitunix_account_fields.py` — dumps raw per-coin JSON + per-field breakdown + sum-of-seven vs corrected sum. Read-only; no orders touched. Run via `cd /home/azureuser/trading_corp && PYTHONPATH=$PWD KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ ./venv/bin/python scripts/verify_bitunix_account_fields.py`. Useful next time BitUnix balance fields look suspect.

**Backup tag:** `pre-bitunix-equity-fix-20260510-0419`

**Verification:**
- PID 185236 → 186736 (clean restart). Service `active`.
- Startup log: `BitunixBroker connected (account=bitunix-futures, equity=$3381.97, 0 positions)` ✓
- Direct broker probe via `BitunixBroker.snapshot()` returns `equity=$3381.97 cash=$3381.97 buying_power=$3381.97 positions=0` — matches BitUnix UI Total Equity.
- `/` and `/division/bitunix_futures` both HTTP 200 post-deploy.
- Polymarket resolver + equity snapshot loops still ticking unchanged (resolver: scanned=8 pending=8; same numbers as pre-restart).
- Fidelity broker errors in journal are pre-existing/unrelated (bot-detection on the Fidelity login page, present since ~16:42 UTC May 09).

**Inert / dormant:**
- Display-only fix today. BitUnix is read-only standby (Phase 1) — no sizing math, risk caps, or `auto_execute_caps` percentages currently consume this number.
- **Becomes load-bearing at Phase 4** (live order placement) — risk/sizing math reading the broker's equity would have oversized 2×. Fix lands well before that gate.

**Memory updates:**
- `trading_corp_bitunix_vision.md` — Phase 1 entry now contains a 2026-05-10 retraction of the 2026-05-03 "transfer is additive" claim, with the corrected formula recorded inline.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-equity-fix-20260510-0419; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/brokers/bitunix.py.\$TAG \$BASE/trading_corp/brokers/bitunix.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-10 03:28 UTC — Polymarket dashboard data layer: round-trips + equity-history persistence (gaps A + B)

**Triggered by:** Board reviewed betmoar.fun dashboard 2026-05-09; asked for any data-persistence gaps to be closed NOW so the eventual UI rebuild has a complete dataset to render. Gap analysis identified A (resolved round-trips) + B (5-min equity snapshots) as the two highest-leverage closures. Gap C (open-positions cache) and the dashboard UI itself moved to BACKLOG (P3) — the data layer is the precondition.

**Files deployed (3):**
- `trading_corp/persistence/db.py` — SCHEMA additions: `polymarket_round_trips` (UNIQUE on order_id, INSERT OR IGNORE-safe) + `polymarket_equity_history` (5-min cadence, append-only). Both protected by `CREATE TABLE IF NOT EXISTS` so init_db() picks them up at startup with no migration script.
- `trading_corp/agents/polymarket_resolver.py` — NEW. `resolve_pending_round_trips(db_url, broker)` walks `would_have_placed` rows whose order_id is missing from `polymarket_round_trips`, looks up resolution via `broker.get_market_resolution`, computes binary-outcome P&L, INSERTs one row. `write_equity_snapshot(db_url, division, broker)` calls `broker.snapshot()` + appends one row. Plus `start_resolver_loop` (3600s) and `start_equity_snapshot_loop` (300s) helpers. Mirrors paper_trade_replay's pattern.
- `trading_corp/main.py` — wires both loops alongside polymarket_arb_task. Cancellation handling in finally block. Graceful no-op if broker absent (logs warning, leaves task None).

**Features shipped:**
- **Hourly resolver (gap A live):** every 60 min, walks unresolved `would_have_placed` rows for `polymarket_arbitrage`, persists resolved P&L to `polymarket_round_trips`. INSERT OR IGNORE keyed on `order_id` so the loop is idempotent. First tick at startup confirmed: scanned=8 / pending=8 / errors=0 (none resolved yet — markets started today).
- **5-min equity snapshot (gap B live):** every 300s, calls `broker.snapshot()` + appends `(ts, division, equity, cash_usdc, positions_value, n_positions)`. First snapshot landed at `2026-05-10T03:28:18+00:00`: equity=$500.00 / cash_usdc=$500.00 / positions_value=$0 / n_positions=0. Matches the funded wallet state.

**Notable code changes:**
- The resolver join uses `json_extract(payload_json, '$.order_id')` to LEFT JOIN audit_event against polymarket_round_trips. `_fetch_unresolved_orders` returns only rows where the round-trip is missing, so a tick is bounded by the actual unresolved backlog (typically <100). `max_per_tick=100` clamps gamma-api calls per tick regardless.
- `positions_value` derived as `max(0, equity - cash)` rather than summing per-position market values — robust against position-shape drift in `data-api.polymarket.com/positions` (the field-name shape isn't pinned to a verified non-empty response yet). Tighten when first non-empty positions response is observed.
- Backtester (`scripts/backtest_polymarket_arbitrage.py`) is unchanged + still works for ad-hoc Board memo runs. Backtester computes everything in-memory each invocation; the resolver persists for dashboard reads. Slight redundancy is intentional — Backtester is for one-shot decision support, resolver feeds the always-on dashboard.

**Latent bug caught + fixed:**
- First boot crashed the resolver loop with `TypeError` from `log.info("polymarket_resolver tick: %s", counts)` — the prod RedactingFilter rewrites dict args into their keys, breaking %-style format. Known prod gotcha (per memory `trading_corp_prod_ops`). Fixed by switching to f-string. Two patch deploys for this entry.

**Verification:**
- PID 184728 → 185250 (final restart with f-string fix). Service `active`. Both loops in startup logs:
  ```
  polymarket round-trip resolver online (interval=3600s)
  polymarket equity snapshot writer online (division=polymarket_arbitrage, interval=300s)
  polymarket_resolver tick: {'scanned': 8, 'resolved': 0, 'pending': 8, 'void': 0, 'not_found': 0, 'errors': 0}
  ```
- DB inspection confirms both tables exist + first equity row landed: 1 row in polymarket_equity_history, 0 rows in polymarket_round_trips (correct — markets haven't resolved).

**Inert / dormant:**
- `polymarket_round_trips` will start filling as today's paper trades' markets begin resolving (next 12-72 hours depending on category). First wave will be sports markets that resolve same-day or next-day. Politics/longer-tail markets fill in over the week. Dashboard build-out (P3 BACKLOG) blocked on having ~30 resolved rows for meaningful inference.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-data-gaps-20260510-0328; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/main.py.\$TAG \$BASE/trading_corp/main.py; \
mv \$BASE/trading_corp/persistence/db.py.\$TAG \$BASE/trading_corp/persistence/db.py; \
rm -f \$BASE/trading_corp/agents/polymarket_resolver.py; \
sudo systemctl restart trading-corp
"
```
*(Note: rollback removes the new tables but leaves any data already written in them — `DROP TABLE polymarket_round_trips; DROP TABLE polymarket_equity_history;` if you want a clean slate.)*

---

## 2026-05-10 02:51 UTC — Polymarket: warm-and-fan parallel LLM calls + K=10→20

**Commit:** `969c6ab` — 2 files, 39 insertions / 6 deletions.
**Triggered by:** Board direction 2026-05-10 — Polymarket needs faster reaction; sequential K=10 was making cycles ~80s apart instead of the intended 30s.

**Changes:**
- `polymarket_arbitrage.py:run_scan_cycle` — warm-and-fan parallel pattern. First LLM call serial (warms Anthropic prompt cache); remaining K-1 fire via `asyncio.gather`. Cycle time: ~50s sequential → ~10s parallel. Prompt-cache hits preserved (the cache prefix is hot before fan-out).
- `strategies.yaml polymarket_arbitrage.k_markets_per_cycle: 10 → 20`. Doubles unique markets evaluated per cycle. Cooldown 6h still bounds daily LLM cost; daily ~$2-50 → ~$4-100 worst case.
- `asyncio.gather(return_exceptions=True)` — single LLM failure becomes None in the estimates list; cooldown still advances; per-market loop skips. Order preserved via `zip(survivors, estimates)`.

**Backup tag:** `pre-warm-fan-parallel-20260510.tar.gz`.
**Verification:** PID 182852 → 183604 (clean). Post-restart cycle at 02:52:15 shows `k_per_cycle: 20` ✓. Cycle currently finds `survivors_post_filter: 0` because all 22 eligible markets are in 6h cooldown from earlier today's runs (earliest expires 08:06 UTC). Parallel LLM behavior will exercise naturally as cooldowns expire ~03-05 hours from now. Audit-row timestamp pattern will show the change: previously 5s gaps between K calls; now one 5s warm + tight burst of ~19 calls within ~5s.

**Anthropic limits — not a constraint.** At tier-3 (4000 RPM), running K=20 + parallel = ~30 req/min worst case = 99.3% headroom. Cost is bounded by 6h cooldown, not rate limits.

---

## 2026-05-10 02:31 UTC — Polymarket UX rework: rich activity tiles + LLM analysis right-rail + reasoning persistence

**Commits:** `4bcaf14` (Phase 1 — reasoning persistence) + `f81ae5c` (Phase 2 — UI).
**Triggered by:** Board feedback 2026-05-10 ~02:15 UTC: *"WOULD HAVE PLACED tells me nothing about the trade. Expert Analysis tile should show the LLM decision. I would like the expert llm analysis to be saved as a point in time static snapshot."*

**Phase 1 — reasoning persistence (4bcaf14):**
- `polymarket_llm_probability_called` audit row now carries `llm_reasoning` (full LLM justification text), `key_unknowns`, `question` (full market question), `would_emit` flag, `resolves_at`. Was missing the reasoning text — load-bearing for fine-tuning.
- ProposedOrder.extra extended with same fields; `would_have_placed` payload pulls them in main.py.
- Storage cost negligible (~5-20MB/month at K=10/30s saturation).

**Phase 2 — UI rework (f81ae5c):**
- `_query_division_activity` now includes `polymarket_llm_probability_called` + `polymarket_order_rejected_by_risk` kinds (NOT scan_cycle — would flood). Each row gets a `polymarket: dict | None` sub-shape with all fields needed for rich tile rendering.
- `division.html` activity-row template branches on `evt.polymarket` for rich layout: market_slug + BUY YES/NO badge + category/series chips + market question (line-clamp-2) + probability strip (LLM% vs market% vs Δ% vs sizing) + 200-char reasoning preview (italic). Risk-rejected variant surfaces risk_reason in red.
- New endpoint `GET /partials/polymarket-analysis/{event_id}` + `partials/polymarket_analysis.html`. "Show analysis →" button on each tile loads full LLM snapshot into the right rail via HTMX. Right rail shows: kind+ts header, market question, 3-card prob grid (LLM YES / market YES / divergence + threshold), decision (outcome + sizing + skip/risk-reject indicators), full LLM reasoning in preformatted block, key unknowns list, resolution metadata + audit event id.
- Right rail empty-state copy differentiates Polymarket ("Click 'Show analysis →' on any LLM call…") from PMCC ("Click any position…").

**Backup tag:** `pre-polymarket-rich-ui-20260510.tar.gz` (51K).
**Verification:** PID 181134 → 182852 (clean restart). Endpoint smoke: `GET /partials/polymarket-analysis/{latest_id}` returns 200 with rendered analysis (sample: hantavirus market, LLM 97% YES). Division detail page returns 200 with "Show analysis" buttons + 7 polymarket rows visible (mix of would_have_placed + evaluated-skipped). 27 polymarket tests pass; full suite green.

**Inert / dormant on current traffic:** none — all changes are live now. Future LLM calls (every 30s) persist full reasoning to audit_event; new tile rendering applies retroactively to existing rows where the data is available, gracefully degrades for rows without the new fields.

**Known gap:** the existing Polymarket audit rows from before commit 4bcaf14 (the 5 LLM-called rows from earlier today) lack `llm_reasoning` in their payload, so their right-rail analysis shows blank reasoning. Future rows complete; not worth backfilling.

**Rollback:** `tar xzf /home/azureuser/backups/pre-polymarket-rich-ui-20260510.tar.gz && rm -f trading_corp/web/templates/partials/polymarket_analysis.html && sudo systemctl restart trading-corp`.

---

## 2026-05-10 02:05 UTC — Polymarket: skip HITL, flip enabled:true; strategy LIVE in paper-mode

**Commit:** `897607a` — 2 prod files (`main.py` + `config/strategies.yaml`), 118 insertions / 37 deletions.
**Triggered by:** Board direction 2026-05-10. Per-trade `/approvals/{order_id}` click gate determined to be net-friction without proportionate protection given Polymarket's bounded blast radius ($1 fixed sizing × $1K aggregate cap × deterministic-Python risk gate). Polymarket's fast-moving prices made the HITL latency a real drag on opportunity capture.

**Architecture change:**
- `_scheduled_polymarket_arb_loop` now calls `risk_agent.evaluate()` inline instead of routing through `_run_order(graph, ...)`. Approved orders log `would_have_placed` directly; rejected orders log `polymarket_order_rejected_by_risk`. Risk gate is still load-bearing per CLAUDE.md §1 — every order flows through the deterministic Python caps; LLM hallucination cannot bypass them.
- `polymarket_arbitrage.enabled: false → true`. Strategy is live in paper mode (broker still ReadOnlyBroker; nothing actually trades; rows accumulate for Backtester).
- Telegram message changed from "routing for approval" to "logged to activity rail" — visibility-only, not gating.
- `auto_execute: false` stays (moot today; Phase 3 will add live signing path + auto_execute_caps + daily kill switch + daily summary digest as the safety scaffolding equivalent to per-trade HITL).

**Backup tag:** `pre-polymarket-direct-log-20260510.tar.gz` (34K).
**Verification:** PID 180231 → 181134 (clean). Boot log:
```
PolymarketBroker connected (funder=***REDACTED***, equity=$500.00, 0 positions)
Polymarket arbitrage scanner online (enabled=True, auto_execute=False, hitl=DIRECT)
```

**End-to-end live activity within 2 minutes of restart:**
- 2 scanner cycles (02:05:19, 02:06:32) — 64 markets pre-filtered each → 10 survivors per cycle
- 5 LLM calls completed across both cycles, ~5s each (Anthropic prompt cache hit on follow-ups)
- First cycle's 02:07:46 order-emission burst: **4 `would_have_placed` rows** (3 BUY NO at 0.84/0.16/0.12; 1 BUY YES at 0.05). All sized correctly to ~$1 USDC notional. Risk gate approved all 4 — no rejections.
- Activity rail on /division/polymarket_arbitrage now showing real-time strategy reasoning chain end-to-end.

**Operational expectations going forward:**
- Scanner ticks every 30s (`poll_interval_sec`). Each tick runs ~50s when emissions fire (10 sequential Anthropic calls); tick-to-tick spacing absorbs the latency.
- Daily LLM cost: $2-50/day depending on cooldown saturation.
- Daily would_have_placed rows: highly variable; 4 in the first cycle is unusually high (LLM is "hot" on extreme-divergence calls). Realistic steady-state TBD as cooldown-bound cycles average out.
- One sanity-check row in the first burst: mlb-nym-ari at implied 0.05 with LLM-claimed prob 0.95 (90% divergence). Either real value or LLM hallucinated the matchup. Backtester will surface which.

**Phase 2.5 + 2a + Phase 0/1 are now all complete + LIVE.** Backtester will run on accumulating paper rows; verdict gates the eventual Phase 3 (live order placement) decision.

**Rollback:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-direct-log-20260510
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
sudo systemctl restart trading-corp
"
# This restores the HITL approval flow + sets enabled:false again.
```

---

## 2026-05-10 01:47 UTC — Polymarket Phase 2.5: Backtester binary-outcome extension

**Commit:** `a01dd4b` — 4 files (1 prod broker + 1 prod script + 1 test + 1 runbook), 799 insertions / 0 deletions.
**Triggered by:** Phase 2.5 minimal-viable per memo Q4. Phase 2a strategy must accumulate ≥30 days of paper would_have_placed rows before this gate is meaningful; gate is for the future `auto_execute: true` flip, not the `enabled: true` flip (paper-mode HITL is the safe path between).

**Three pieces shipped:**
- `brokers/polymarket.py` — new `get_market_resolution(condition_id, slug)` two-pass gamma-api lookup. Decodes resolution from `outcomePrices` + `umaResolutionStatus` per gamma-api conventions verified live (resolved/pending/void/not_found).
- `scripts/backtest_polymarket_arbitrage.py` — replay tool. Reads paper rows over a horizon, computes binary-outcome P&L (`won → qty × (1-price)`, `lost → -qty × price`), aggregates: hit rate / wins-losses / total notional / total P&L / ROI / avg + median P&L / max consecutive-loss DD / per-category breakdown. Heuristic verdict (RECOMMEND_APPROVAL / REJECTION / MIXED_SIGNAL / INSUFFICIENT_DATA).
- `runbooks/polymarket_arbitrage_backtest.md` — Board runbook: when to run, how to interpret each output section, Board memo template for approval/rejection decisions, FAQ.

**Backup tag:** `pre-backtester-phase25-20260510.tar.gz` (9.6K).
**Verification:** PID 179088 → 180231 (clean). Polymarket still $500 live + redacted in logs; scanner online (enabled=False). 19 new pytest cases pass (P&L math 4 directions, skip semantics, aggregation incl. monotone-up max-drawdown edge case, all 4 verdict thresholds). Script runs cleanly on prod against live DB — returns `NO_DATA` (correct: strategy disabled, no paper rows yet).

**Post-flip workflow (when Board enables strategy in paper-mode):**
1. Paper rows accumulate for 30+ days
2. `python scripts/backtest_polymarket_arbitrage.py --days 30` produces verdict
3. If `RECOMMEND_APPROVAL`, write Board memo per the template + flip `auto_execute: true`
4. If `REJECTION`, investigate (per-category breakdown often reveals which strategies-within-strategy work) or stay paper-mode

**Inert until enable.** All Phase 2a + 2.5 infrastructure in place; the gate for `auto_execute: true` exists. Strategy still `enabled: false`.

**Rollback:** `tar xzf /home/azureuser/backups/pre-backtester-phase25-20260510.tar.gz && rm -f scripts/backtest_polymarket_arbitrage.py && sudo systemctl restart trading-corp`.

---

## 2026-05-10 01:26 UTC — Polymarket Phase 2a Step 5: gamma-api query tuning + two-layer category mapping

**Commit:** `33169ae` — 2 prod files (`brokers/polymarket.py` + `agents/strategies/polymarket_arbitrage.py`) + 1 test file (210 insertions / 15 deletions).
**Triggered by:** Phase 2a pre-enable checklist Step 5. Default gamma-api page sort returned long-tail markets first — original `list_markets` query yielded 0 markets within the 7-day cap. Tuned empirically to a server-side query that returns 66+ markets passing all Phase 2 caps per cycle.

**Changes:**
- `list_markets` now uses `order=volume24hr&ascending=false&end_date_min=NOW+min_hours&end_date_max=NOW+max_days` for server-side filter alignment with the strategy's client-side caps.
- New `_classify_market(market) -> (top_category, series_subtag)` with 8 keyword-set buckets (sports / politics / geopolitics / finance / crypto / entertainment / celebrity / health / other). Tested empirically against 66 live markets — 100% classified, 0 in "other" bucket.
- Strategy threads BOTH levels: LLM prompt context (`Category: {top} ({series})` for base-rate priors), `ProposedOrder.extra.category` + `extra.series`, audit row `polymarket_llm_probability_called.{category, series}`.

**Backup tag:** `pre-gamma-tuning-20260510.tar.gz` (13K).
**Verification:** PID 178354 → 179088 (clean). All brokers reconnected; Polymarket still $500 live + redacted in logs; scanner online (enabled=False). 27 polymarket tests pass (8 new for category classification + 19 existing); 508-test suite green; pre-existing PMCC LEAP-fixture failures unchanged.

**Inert until enable.** Strategy still `enabled: false` in `strategies.yaml` — gates on Phase 2.5 Backtester verdict (next task).

**Rollback:** `tar xzf /home/azureuser/backups/pre-gamma-tuning-20260510.tar.gz && sudo systemctl restart trading-corp`.

---

## 2026-05-10 01:04 UTC — BAL CHG row ts_short pinned to bar_ts (cosmetic, sibling-row alignment)

**Commit:** `c94df37` — 2 files (`main.py` + `web/data.py`), 17 insertions / 6 deletions.
**Triggered by:** Board screenshot 2026-05-09 ~20:46 UTC. The BAL CHG row landed at `05-09 20:02 ET` (audit-row write time = bar close + ~2min) while its sibling donchian_evaluated row showed `05-09 14:00 ET` (bar open). Same evaluation cycle, but the 6h visual gap reads as two unrelated events.
**Fix:** orchestrator (`main.py:_run_donchian_bar`) now stamps `bar_ts` on the balance_change payload before logging; rendering layer (`web/data.py:build_donchian_view`) prefers `payload.bar_ts` over `r["ts"]` for the BAL CHG `ts_short`, mirroring the existing donchian_evaluated logic. Defensive fallback to audit ts for legacy rows that pre-date the stamp.
**Backup tag:** `pre-balchg-ts-fix-20260510.tar.gz` (38K, 2 modified files).
**Verification:** PID 177477 → 178354 (clean). All brokers reconnected (Polymarket still $500.00 live, BitUnix $6763.94 — the P2 transfer bug is unchanged, expected). Polymarket scanner online (enabled=False, no-op). Donchian scheduler online (enabled=True). Existing BAL CHG row in the DB at 2026-05-09 20:02 ET will continue displaying its audit-write-time until it ages out of the 60-row window (no payload migration). Next BAL CHG row — when fired — will display the bar's open time aligned with its donchian_evaluated sibling.
**Rollback:** `tar xzf /home/azureuser/backups/pre-balchg-ts-fix-20260510.tar.gz && sudo systemctl restart trading-corp`.

---

## 2026-05-10 00:39 UTC — Polymarket wallet went live (KV upload + service restart)

**Not a code deploy** — wallet/secrets bring-up. Board completed steps 1-4 of the Phase 2a pre-enable checklist between 2026-05-09 22:00 UTC and 2026-05-10 00:30 UTC: generated EOA via `eth_account.Account.create()` (regenerated once after losing the first address — wallet wasn't funded, zero loss), Alchemy Polygon Mainnet signup + RPC URL, $500 native USDC + 98 POL funded from Coinbase to the EOA on Polygon mainnet, `az keyvault secret set` for the three secrets.

**On-chain verification (pre-restart, public RPC):** native USDC `0x3c49…3359` = $500.00, USDC.e bridged = $0.00 (no misrouted tokens), POL/MATIC = 98.375 (~$39 at $0.40/POL — way more than needed for gas).

**KV state confirmed (presence-only, no values exposed):**
- `POLYMARKET-PRIVATE-KEY`: enabled, length 64 (no `0x` prefix; `eth_account.Account.from_key` accepts both forms — harmless for Phase 1 since signing isn't in the path)
- `POLYMARKET-FUNDER-ADDRESS`: enabled, length 42 (`0x` + 40 hex ✓)
- `POLYGON-RPC-URL`: enabled, length 62 (sensible for Alchemy or public RPC)

**Pre-restart NSG actions:** Board's laptop IP rotated TWICE during this session (`98.231.16.63` → `73.104.119.214` mid-session for the Phase 2a code deploy; rotated back to `98.231.16.63` for the wallet bring-up). Both updated cleanly via `az network nsg rule update` per `auth_lockout_recovery.md`.

**Restart:** PID 176618 → 177477 (clean). Boot log:

```
PolymarketBroker connected (funder=***REDACTED***, equity=$500.00, 0 positions)
PaperBroker connected (account=paper_polymarket_copy_trading, equity=$0.00)
Polymarket arbitrage scanner online (enabled=False, auto_execute=False)
```

**Three things confirmed by that one log line:**
- USDC balance reads cleanly from Polygon RPC via `eth_call(USDC.balanceOf)`.
- RedactingFilter scrubs the funder address from log output (literal-value redaction registered in `secrets.py:load_secrets()`; the address is in memory + KV but never in logs).
- `data-api.polymarket.com/positions?user=…` returned empty — correct for fresh wallet.

**Dashboard verification:**
- Home tile **Polymarket Arbitrage** = `$500.00` (was `$0 STUB`)
- `/division/polymarket_arbitrage`: Equity $500.00 / Cash $500.00 / Buying Power $500.00
- `polymarket_copy_trading` tile = `$0.00 STANDBY` (paper-fallback by design until Phase 4+)

**Phase 2a pre-enable checklist status (steps 5-7 remaining, all server-side):**

| # | What | Status |
|---|---|---|
| 5 | Tune gamma-api query (current default page sort returns long-tail markets that fail 7-day cap) | Next session |
| 6 | Phase 2.5 Backtester (binary-outcome replay, minimal-viable) | Next session — gates Phase 3 |
| 7 | Flip `polymarket_arbitrage.enabled: true` in `strategies.yaml` | After 5 + 6 + Board "go" |

**Rollback recipe (if needed):**

```bash
# Rollback the wallet-going-live state by removing the secrets from KV.
# Service restart after this puts the broker back into stub mode.
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYMARKET-PRIVATE-KEY
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYMARKET-FUNDER-ADDRESS
az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name POLYGON-RPC-URL
ssh azureuser@trading.jacksumner.com "sudo systemctl restart trading-corp"
```

(The wallet itself remains funded on-chain regardless. Code rollback recipe for the Phase 2a code deploy is in the previous deploy_log entry.)

---

## 2026-05-09 21:57 UTC — Polymarket Phase 2a: arbitrage scanner + risk caps + scheduler wiring

**Commits:** `fe757e2` (Phase 2a, committed pre-deploy).
**Triggered by:** Phase 2 strategy build (greenlit by Board after the Phase 1 ship + 5-question memo answers earlier same day). Path B chosen for the LLM call (direct Anthropic, NOT through Research firm — Thesis schema doesn't fit prediction-market probability queries; Polymarket arbitrage is single-division decision logic, not cross-division knowledge work). All 9 risk caps confirmed verbatim from Q1 answer; K=10 / 6h cooldown from Q3 answer 'a'; 7-day max horizon from Q2; defensive httpx rate-limiting from Q5.
**Backup tag:** `pre-polymarket-phase2a-20260509.tar.gz` at `/home/azureuser/backups/` (5 modified files; 2 new files have no pre-state).
**Pre-deploy NSG action:** Board's laptop IP rotated mid-session (Comcast); old `98.231.16.63` → new `73.104.119.214` updated on `tc-prod-nsg/AllowSSHFromHome` via the standard `auth_lockout_recovery.md` Cloud-Shell-or-az-CLI path. Documented as the correct recovery; not a deploy concern.

**Files deployed (5 modified, 2 new):**

- `config/risk.yaml` — new `polymarket:` top-level block. All 9 caps from Q1 answer (universe pre-filter: min volume $50K / max spread 3¢ / min ttr 24h / implied-prob bounds 5-95%; per-order: 5%-of-equity / $250 single-market; aggregate: 25%-equity-cap-$1K daily / $1K total open).
- `config/strategies.yaml` — new `polymarket_arbitrage:` block (enabled:false, auto_execute:false, K=10, 6h cooldown, 7d horizon, fixed_usdc/$1 sizing). Plus a documented `polymarket_copy_trading:` placeholder for Phase 4+.
- `trading_corp/agents/risk.py` — new `_evaluate_polymarket()` branch routed by the `is_prediction_market` extra flag. Atomic + aggregate caps; halt checks still run BEFORE the polymarket branch. Daily-aggregate cap queries audit_event for today's `would_have_placed`/`board_approved`/`filled` rows; total-open cap returns 0 in Phase 2a (Phase 3 implements). `evaluate()` signature gained an optional `db_url` kwarg (back-compat: existing callers don't pass it, aggregate checks no-op).
- `trading_corp/brokers/polymarket.py` — new `list_markets(filters)` method against gamma-api with deterministic Python-side filtering. New `_http_get_json()` helper with concurrency cap (semaphore=6) + 429 backoff (max 4 retries, 1-30s window with jitter).
- `trading_corp/main.py` — new `_scheduled_polymarket_arb_loop()` spawned alongside `donchian_task`. Re-reads `poll_interval_sec` each tick so config changes don't need a restart. Routes emitted ProposedOrders through `_run_order` (existing risk + HITL graph). Telegram pings on each emission.
- `trading_corp/agents/strategies/polymarket_arbitrage.py` — **NEW.** PolymarketArbitrageAgent. mtime-cached config, per-market 6h cooldown in agent_state (single-JSON-blob with cleanup-on-load). Direct Anthropic call via `agents.llm.build_chat_model`. Permissive JSON parser handles prose-wrapped output; clamps prob_yes to [0.01, 0.99]; normalizes unknown confidence to "medium". Defensive implied-prob extraction handles outcomePrices-as-string, outcomePrices-as-list, lastTradePrice, price.
- `trading_corp/agents/strategies/_polymarket_prompts.py` — **NEW.** Shared analyst-persona system prompt (~1554 estimated tokens). Imported by arbitrage today, by future copy_trading later — Anthropic's prompt cache amortizes the input-token cost across both strategies (5-min ephemeral TTL; ≥1024-token threshold cleared with substantive methodology + worked example).

**Features shipped (load-bearing for future "is X done?" checks):**

- The Polymarket scanner loop is online but inert. Boot log:
  `"Polymarket arbitrage scanner online (enabled=False, auto_execute=False)"`. To activate: Board flips `polymarket_arbitrage.enabled` in `strategies.yaml` AND uploads the 3 KV secrets.
- Risk gate now routes prediction-market orders by `extra.is_prediction_market` flag — clean separation from PMCC/crypto cap logic.
- Anthropic prompt-cache-ready system prompt is in the codebase; both Polymarket strategies will share it. ~85% input-token cost reduction on K-1 follow-up calls per cycle.
- 19 new pytest cases in `tests/test_polymarket_arbitrage.py` regress: config defaults, implied-prob extraction (4 shapes), JSON parse robustness (clean/prose/OOB/garbage/unknown-confidence), risk-gate cap matrix (approve, implied-bound rejects, single-market $ cap, per-position % cap, halt-precedence, non-polymarket-isolation).

**Notable code changes (callouts a future Claude shouldn't miss):**

- The strategy emits ProposedOrder.extra with `is_prediction_market: True`. **The risk gate routes EXCLUSIVELY off this flag**, not off `order.strategy == "polymarket_arbitrage"`. When `polymarket_copy_trading` ships, it should set the same flag — that single change makes it inherit all 9 caps without modifying risk.py.
- `RiskAgent.evaluate()` gained an optional `db_url` kwarg for the daily-aggregate cap query. Existing callers (PMCC, donchian, otter, cypher, manual) don't pass it; their behavior is unchanged. The Polymarket scheduler in main.py needs to start passing it once `enabled: true` flips and aggregate caps actually bind.
- The shared analyst prompt is intentionally substantive — methodology + worked example clear the 1024-token cache threshold. Trimming the prompt below ~1300 tokens would silently disable the cache and quintuple input costs at K=10/30s.
- Aggregate query (`_sum_polymarket_today`) uses `substr(ts, 1, 10)` for date partitioning. Switches to UTC midnight; if Board ever wants ET-based daily aggregates, that's a single-line change but flag the semantics in audit.

**Verification:**

- Pre-restart PID 175242 → post-restart 176618 (clean).
- All 7 prod files match local LF-normalized md5s exactly after SCP.
- Boot log:
  - `Registered polymarket broker for division=polymarket_arbitrage (paper=False)` ✓
  - `Registered paper broker for division=polymarket_copy_trading (paper=True)` ✓
  - `PolymarketBroker connected as STUB (missing funder or RPC URL)` — expected (KV uploads still pending Board action)
  - `Polymarket arbitrage scanner online (enabled=False, auto_execute=False)` — exactly the inert posture Phase 2a ships
- KV fetch attempts for `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` returned empty (graceful fallback to stub).
- 19 new tests pass; 9 existing risk_gates tests pass unchanged; 508 tests in the broader suite pass.
- Live test of `list_markets()` against gamma-api (executed pre-deploy): real markets fetched, 7 returned with default filter, 0 returned with Phase 2 caps applied (gamma-api default page sort isn't volume-first; tuning the query is a pre-enable follow-up flagged in the file's docstring).

**Inert / dormant on current traffic:**

- Scanner loop wakes every 30s, no-ops on `enabled: false`. **Zero LLM calls; zero cost.**
- Cooldown table in agent_state stays empty until first cycle with `enabled: true`.
- Aggregate-cap query (`_sum_polymarket_today`) returns 0.0 — no Polymarket audit rows yet.
- `polymarket_copy_trading` tile remains paper-fallback STANDBY $0.

**Pre-enable checklist (Board action):**

1. Generate wallet (`python3 -c "from eth_account import Account; ..."`).
2. Sign up at alchemy.com, copy Polygon Mainnet HTTPS URL.
3. Fund EOA with $500 native USDC + ~$5 MATIC on Polygon.
4. Upload to KV: `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` / `POLYGON-RPC-URL`.
5. Tune `gamma-api/markets` query to surface high-volume / short-tail markets (current sort returns long-tail first; Phase 2 caps reject them).
6. Phase 2.5 Backtester verdict (replay-only minimal-viable; greenlit but not yet built).
7. Flip `polymarket_arbitrage.enabled: true` in `strategies.yaml` (no service restart needed — mtime-cached).
8. Watch the activity rail on `/division/polymarket_arbitrage` for `would_have_placed` rows.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-phase2a-20260509
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/agents/strategies/_polymarket_prompts.py \
      trading_corp/agents/strategies/polymarket_arbitrage.py
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 20:13 UTC — Polymarket Phase 1: read-only broker + division wiring (+ Phase 0 secrets backfill caught at deploy)

**Commits:** `db1f0cd` (Phase 0 secrets, never previously deployed) + `d7cbea2` (Phase 1 broker + wiring, committed pre-deploy).
**Triggered by:** Polymarket Arbitrage division scope (multi-message in-session brief; see CLAUDE.md §6 STOP-AND-ASK items resolved 2026-05-09 ~17:30-19:00 UTC). Phase 0.5 EU egress proxy was scoped, then ruled NO-GO by the smoke test — Polymarket's read APIs serve tc-prod-vm's US-east IP without geo-block. Phase 1 ships read-only adapter + tile rendering inert; goes live the moment the KV secrets land.
**Backup tag:** `pre-polymarket-phase1-20260509.tar.gz` (21K, 4 modified files) at `/home/azureuser/backups/`. Plus an extra `secrets.py.pre-polymarket-phase1-20260509.bak` snapshot for the secrets.py rollback (because Phase 0 was caught mid-deploy — see Notable below).

**Files deployed (5 modified, 1 new):**

- `trading_corp/utils/secrets.py` — Phase 0 backfill caught at deploy time. Three new fields on `Secrets` (`polymarket_private_key`, `polymarket_funder_address`, `polygon_rpc_url`). New `register_redact_literal()` mechanism + `_REDACT_LITERALS` set for value-substring scrubbing of secrets that third-party libs may log raw. KV expected_env_vars extended. Three new entries on `_SECRET_KEY_NAMES` for KEY=value redaction.
- `trading_corp/brokers/base.py` — `ReadOnlyBroker` ABC extracted (connect/disconnect/snapshot/quote). `Broker` now subclasses it (adds place_order + cancel_order). Behavior-zero change for existing brokers; PolymarketBroker is the first ReadOnlyBroker subclass.
- `trading_corp/brokers/polymarket.py` — **NEW.** PolymarketBroker(ReadOnlyBroker). Stub mode if creds missing. snapshot() = USDC balance via Polygon RPC `eth_call` + positions via data-api. quote() = gamma-api slug→token_id then clob last-trade-price. `signature_type=EOA` pattern (signer == funder, no Polymarket proxy/SAFE) — Path A wallet model. NO place_order method exists; ABC enforces read-only.
- `trading_corp/main.py` — `_build_broker_for_division` polymarket family branch. No PaperExecutionBroker wrap (ReadOnlyBroker has no order surface to simulate).
- `trading_corp/utils/divisions.py` — new "polymarket" investment-type group between Crypto and Retirement. Slug-prefix classification handles the paper-fallback copy-trading division (broker=paper but slug starts with `polymarket_`).
- `config/divisions.yaml` — two new entries: `polymarket_arbitrage` (broker=polymarket, real adapter, standby) + `polymarket_copy_trading` (broker=paper, $0 placeholder for Phase 4+ copy-trading strategy, standby).

**Features shipped (load-bearing for future "is X done?" checks):**

- Home dashboard renders a new "Polymarket" investment-type group (4th group, between Crypto and Retirement) with TWO tiles: "Polymarket Arbitrage" + "Polymarket Copy Trading". Both render STANDBY today.
- ReadOnlyBroker ABC is now in the codebase. The Fidelity migration TODO from CLAUDE.md §7 sharp edges is now strictly possible (separate cleanup; not done here).
- Phase 0 secrets-loader for Polymarket creds + literal-value redaction is live on prod.
- The 2026-05-09 EU-egress smoke test runbook (`runbooks/eu_proxy_smoke_test.md`) is preserved as the starting point if Phase 3 trade placement turns out to need a proxy.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **Phase 0 was caught at deploy time, not pre-deploy.** I shipped Phase 1 first thinking Phase 0 was already on prod (it was committed locally as `db1f0cd` but never SCP'd — I made a "bundle the deploy with Phase 1" call earlier in the session and forgot to honor it). The service crash-looped on `AttributeError: 'Secrets' object has no attribute 'polymarket_private_key'` for ~90s before I caught it via boot-log inspection, SCP'd `secrets.py`, and restarted clean. Lesson: when a code commit references new fields on a shared dataclass, deploy the dataclass file BEFORE the consumer file, OR deploy as one atomic batch.
- The `polymarket_copy_trading` division uses `broker: paper` deliberately. Both polymarket_* divisions land in the new "Polymarket" investment-type group via slug-prefix classification (utils/divisions.py:_POLYMARKET_SLUG_PREFIX). The arbitrage division's paper-fallback would conflict with broker:polymarket on the same wallet (both tiles would show the same balance) — broker:paper for the second tile keeps it visibly distinct ($0 STANDBY) until Phase 4+ wires the real strategy.
- PolymarketBroker is NOT wrapped in PaperExecutionBroker. The convention for PAPER mode (wrap-real-broker-with-paper-fills) doesn't apply to ReadOnlyBroker subclasses — there's no order surface to simulate. If a future Polymarket division needs paper-mode order simulation (Phase 2 strategy paper-track), the new code path will be `PolymarketLiveBroker(Broker)` in Phase 3, and PaperExecutionBroker will wrap THAT.
- The `private_key` constructor arg on PolymarketBroker is accepted but unused in Phase 1. Phase 3 signing will read from the same arg without a constructor change.

**Latent bugs caught + fixed (if any):** none new. The pre-existing `secrets.py.pre-polymarket-phase1-20260509.bak` confirms prod was running the pre-Phase-0 file before this deploy — no drift content beyond "version skew due to my earlier deferral."

**Verification:**

- Pre-restart PID 171746 → post-restart 175242 (clean).
- All 5 prod files match local LF-normalized md5s exactly after SCP.
- Boot log:
  - `PolymarketBroker connected as STUB (missing funder or RPC URL)` — expected with no KV secrets yet.
  - `PaperBroker connected (account=paper_polymarket_copy_trading, equity=$0.00)` — copy-trading placeholder healthy.
  - `Registered polymarket broker for division=polymarket_arbitrage (paper=False)` ✓
  - `Registered paper broker for division=polymarket_copy_trading (paper=True)` ✓
  - KV fetches for `POLYMARKET-PRIVATE-KEY` / `POLYMARKET-FUNDER-ADDRESS` / `POLYGON-RPC-URL` returned empty (Board hasn't uploaded yet — graceful degradation to stub mode is the design).
- `GET /` returned HTTP 200 in 4.87s, 87.2 KB.
- `<h2>` headers in document order: `Individual` → `Crypto` → `Polymarket` → `Retirement`. Section order matches `_INVESTMENT_TYPE_ORDER`.
- Both Polymarket tiles render with STANDBY badges. 4 STANDBY badges total on home page (2 new Polymarket + 2 existing: Coinbase Futures, BitUnix Futures).

**Inert / dormant on current traffic:**

- PolymarketBroker `snapshot()` and `quote()` return zeros / empty until KV holds the three secrets. After Board uploads them, next service restart brings the arbitrage tile live with real wallet balance + open positions (initially: $500 USDC, 0 positions).
- PolymarketBroker.quote() field-mapping (gamma-api `clobTokenIds` / `outcomes`) is best-effort against unverified shape — first non-empty response from a real market should be eyeballed to confirm. Field names in `_fetch_positions` similarly defensive (.get() with fallbacks); first funded-wallet response should be sanity-checked.
- Phase 3 follow-up tracked as task #31: re-test geo-block on authed/write CLOB endpoints before live order placement. If writes are blocked, revive `runbooks/eu_proxy_smoke_test.md`.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-polymarket-phase1-20260509
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
# Phase 0 secrets.py rollback (separate backup since the tarball was made
# pre-Phase-0-discovery; the pre-Phase-0 file is in its own .bak):
cp /home/azureuser/backups/secrets.py.pre-polymarket-phase1-20260509.bak \
   trading_corp/utils/secrets.py
# Drop the new file:
rm -f trading_corp/brokers/polymarket.py
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 16:42 UTC — Donchian: observe Board-driven balance changes; state-as-source-of-truth

**Commits:** `78e57a0` (committed before deploy).
**Triggered by:** Board direction (chat 2026-05-09 post-UI-cleanup) — recurring weekly BTC buys + occasional cash deposits land on the coinbase_spot account outside the strategy's knowledge. The strategy must observe these (log them, attribute to Board) but NOT auto-flip state in response. Strategy state is now the source of truth for portfolio composition; the broker's balance is normalized to it at the next BUY/SELL signal, not via a forced rebalance trade.
**Backup tag:** `pre-balance-tracking-20260509-utc-pre.tar.gz` at `/home/azureuser/backups/` (43K, 4 modified files; no new files).
**Pre-deploy DB mutation:** `UPDATE agent_state SET value_json='{"state":"cash","cost_basis":null}' WHERE agent='coinbase_btc_donchian' AND key='state';` ran ~1 min before the systemctl restart. Today's earlier startup reconcile (15:23 UTC deploy) had set state=BTC with cost_basis=$80,371.17 — but per the new model, the 0.595 BTC was always Board's, not the strategy's, so CASH is the correct strategy view. Previous values preserved in deploy_log + the row's prior `updated_ts` for rollback.

**Files deployed (4 modified):**

- `trading_corp/agents/strategies/coinbase_btc_donchian_agent.py`:
  - `PersistedState` gains `last_known_cash` + `last_known_btc_qty` (defaults None) — baselines for delta detection.
  - New persistence key `last_known_balances` (alongside existing `state` + `last_bar_ts`).
  - `_loaded_from_db: bool` flag — flips True when `_restore_from_db` finds a state row.
  - `restore_from_broker` now short-circuits when `_loaded_from_db` is True (any subsequent restart trusts persisted state). Also accepts a `cash` arg for first-bring-up baseline seeding.
  - New public method `record_balance_snapshot(*, cash, btc_qty, threshold_usd=1.0, threshold_btc=0.0001) -> dict | None` — compares to last-known, returns audit-payload dict on material delta and advances baseline. First call after bring-up just seeds (no false-positive delta).
  - `on_bar_close` gains optional `cash` kwarg. When supplied, BUY sizing uses cash (not account_equity) — the strategy never double-counts the Board's pre-existing BTC into a new buy notional. Back-compat: `cash=None` falls back to account_equity.
- `trading_corp/main.py`:
  - `_run_donchian_bar` extracts `cash` from `snap.cash`, calls `agent.record_balance_snapshot(cash=cash, btc_qty=held_btc)` BEFORE `on_bar_close`. On material delta, writes a `balance_change` audit-event row (kind=`balance_change`, actor=`coinbase_btc_donchian`).
  - `on_bar_close` now passes `cash=cash`.
  - Startup reconcile call site updated to pass `cash=cash`. Comment block rewritten to document the no-op-after-bring-up semantics.
- `trading_corp/web/data.py`:
  - `build_donchian_view` SQL widened to `kind IN ('donchian_evaluated','balance_change')`. Row build branches on `kind`, producing two distinct shapes: existing decision shape, OR `{kind: 'balance_change', ts_short, attribution, state_at_observation, delta_cash, delta_btc, new_cash, new_btc_qty}`.
- `trading_corp/web/templates/partials/donchian_log.html`:
  - Row loop branches on `r.kind`. balance_change rows render full-width with a BAL CHG tag, signed delta amounts (gain-green for +, loss-red for −), and "→ new totals · state=X" trailer. Subtle warn-tinted bg (changes are normal, not alerts). donchian_evaluated rows unchanged.

**Features shipped (load-bearing for future "is X done?" checks):**

- The strategy is now safe against parallel Board trading. Recurring weekly buys + cash deposits land as `balance_change` audit rows; the strategy passively absorbs whatever the broker reports at the next BUY (sizes off cash, sweeps Board's pre-existing BTC into the position via broker-side aggregation) or next SELL (held_btc from snapshot includes all coins, regardless of who put them there).
- Strategy state (CASH↔BTC) is now persisted-state authoritative. `restore_from_broker` is reserved for first-ever bring-up; subsequent restarts preserve the strategy's view. Today's mid-day flip from BTC (set by 15:23 UTC reconcile) → CASH (manual reset 16:42 UTC) was a one-time correction; future deploys should never need to touch the state row directly.
- Decision-log surface now shows two row kinds interleaved chronologically — strategy decisions + Board-attributed balance deltas — giving a single timeline of "what happened on this account" since the strategy's perspective.

**Notable code changes (callouts a future Claude shouldn't miss):**

- BUY sizing changed semantics: `qty = cash / current_close` (when `cash` supplied) instead of `qty = account_equity / current_close`. Tests that don't pass `cash` keep the old behavior (back-compat). If you ever change `on_bar_close`'s signature, mind the back-compat.
- `record_balance_snapshot` advances the baseline EVEN ON sub-threshold deltas. So a slow drift (e.g., $0.50/day fee bleed) won't accumulate over many bars and eventually trip the threshold as a false aggregate event. If that's ever wanted, change the post-detection update path.
- The strategy's `cost_basis` on a BUY is the fill price of the strategy's own buy — NOT a weighted avg with any pre-existing Board BTC. P&L estimates at the next SELL will be measured from the strategy's fill, which is the cleanest accounting given the strategy can't know what the Board paid.

**Verification:**

- Pre-restart PID 170308 → post-restart 171746.
- All 4 files md5 round-trip MATCH after LF-normalization.
- Boot log:
  - `restored state=cash cost_basis=None last_bar=2026-05-09 06:00:00+00:00 last_known_cash=None last_known_btc=None` — picked up the reset CASH state cleanly; balances correctly None pre-first-snapshot.
  - `persisted state present (state=cash, cost_basis=None) — skipping broker reconcile. Board-driven broker deltas will be observed via record_balance_snapshot per bar.` — new short-circuit fired exactly as designed. Broker showed 0.595 BTC + $39K cash; state stays CASH.
  - `Donchian scheduler: sleeping 4743s until next bar close` — math: 16:42:56 + 4743s ≈ 18:01:59 UTC = 14:02 ET. Next bar evaluation arrives on schedule.
- `GET /division/coinbase_spot`: HTTP 200, 62.5KB, 3.5s. Buying Power tile gone, Donchian chart container present, existing decision-log rows (`05-09 02:00 ET`, `05-08 20:00 ET`) preserved, no BAL CHG rows yet (no balance changes have fired in the new code path).
- `GET /partials/donchian-chart/coinbase_spot`: HTTP 200, 10.3KB, 2.1s. 50 candles, current_bar_ts=2026-05-09T06:00:00 UTC, 0 markers.
- 16 existing agent unit tests pass unchanged. New behavior smoke-tested locally: first-bring-up still reconciles; post-bring-up reconcile is no-op; first record_balance_snapshot seeds without delta; material delta returns payload + advances baseline; sub-threshold returns None + advances baseline; BUY sizes off cash when supplied; back-compat cash=None still works.

**Inert / dormant on current traffic:**

- `last_known_balances` agent_state row will appear after the first `record_balance_snapshot` call (next bar at 18:02 UTC = 14:02 ET). Until then, the row doesn't exist.
- BAL CHG tile rows will appear when the Board's recurring weekly buy or a cash deposit lands. Initial seeding at 18:02 UTC will NOT generate a BAL CHG row (first call seeds without firing delta — by design).

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-balance-tracking-20260509-utc-pre
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
# Restore prior state row (state=BTC, cost_basis=80371.17)
sqlite3 \$BASE/data/trading_corp.db \\
  \"UPDATE agent_state SET value_json='{\\\"state\\\":\\\"btc\\\",\\\"cost_basis\\\":80371.17}' \\
   WHERE agent='coinbase_btc_donchian' AND key='state';\"
# Drop the new last_known_balances row if it landed
sqlite3 \$BASE/data/trading_corp.db \\
  \"DELETE FROM agent_state WHERE agent='coinbase_btc_donchian' AND key='last_known_balances';\"
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 15:23 UTC — Coinbase BTC HODL division-detail UI cleanup

**Commits:** `a9c0461` (committed before deploy).
**Triggered by:** BACKLOG P3 — "Coinbase BTC HODL division-detail UI cleanup" (the top-section P3 added 2026-05-09). Bundles four asks Board greenlit at session start: (1) ts_short fix, (2) Manual Order tile removal, (3) Buying Power tile removal, (4) 6h Donchian price chart.
**Backup tag:** `pre-donchian-uicleanup-20260509-utc-pre.tar.gz` at `/home/azureuser/backups/` (49K, 3 modified files; the 2 new files have no pre-state to preserve).

**Files deployed (3 modified, 2 new):**

- `trading_corp/web/data.py` — `build_donchian_view`: `ts_short` now reads `payload.bar_ts` (canonical bar identifier) with `r["ts"]` fallback for legacy rows. New async helper `build_donchian_chart_data(db_url, display_bars=50)` fetches ~50 6h Coinbase OHLCV bars via ccxt public endpoint, computes rolling 20-bar Donchian high / 6-bar Donchian low / 168-bar SMA mirroring `donchian_btc.evaluate` semantics (preceding-window, current bar excluded), pulls BUY/SELL fill markers from `audit_event` (`would_have_placed` paper + `filled` live; both snap to bar-open via payload `bar_ts`), and returns the full chart payload.
- `trading_corp/web/routes.py` — new endpoint `GET /partials/donchian-chart/{slug}` returns the JSON payload from `build_donchian_chart_data`. 404s for any slug other than `coinbase_spot` (chart is single-strategy at this point); returns `{empty: true}` on OHLCV fetch failure.
- `trading_corp/web/templates/division.html` — Buying Power stat card now hidden for `coinbase_spot` (cash == buying_power on spot crypto); grid drops from 4 to 3 cols when `_hide_bp` is true. Manual Order include block deleted (was gated on coinbase_spot only — partial file preserved untouched). New chart partial included between donchian_state and donchian_log; new `donchian_chart.js` script tag included gated on coinbase_spot.
- `trading_corp/web/templates/partials/donchian_chart.html` — new partial. Header with channel-legend chips + 360px chart container (`#donchian-chart`, `data-division="coinbase_spot"`) + empty-state div for OHLCV-fetch-fail case.
- `trading_corp/web/static/js/donchian_chart.js` — new file. Self-running IIFE: Lightweight Charts setup with candlestick series + 2 dashed line series (20-bar high red / 6-bar low green) + solid SMA series (accent blue), fetches `/partials/donchian-chart/coinbase_spot`, sets candle data + 3 line series + markers, draws horizontal price line at last close + circle marker on current bar. 60s refresh interval. ResizeObserver wired so the chart matches container width.

**Features shipped (load-bearing for future "is X done?" checks):**

- Decision-log column `bar (ET)` now renders bar-open time (e.g. `05-09 02:00 ET`), matching the timestamp embedded in `reason`. Verified live: most recent row shows `05-09 02:00 ET` not `05-09 08:02 ET` (which would be the audit-row write time of the 12:02 UTC eval that happened during deploy).
- Division-detail UI is purpose-built for Donchian: stat trio is Equity / Cash / Today's P&L (no BP), no Manual Order tile, full price-chart visibility into the channel state the strategy is reading.
- 6h price chart with all four BACKLOG-asked overlays: candles, entry-channel ceiling (20-bar high), exit-channel floor (6-bar low), SMA(168) trend filter, plus current-bar highlight (circle marker + last-close horizontal price line). Markers infrastructure is wired but the array is empty until the strategy places its first BUY (next breakout above the 20-bar high while above the SMA).

**Notable code changes (callouts a future Claude shouldn't miss):**

- `build_donchian_chart_data` is the canonical place for chart-side rolling window math. If anyone changes the lookback semantics in `donchian_btc.evaluate`, mirror it here too (preceding-window, current bar excluded — current bar's high/low DOES NOT count toward its own donchian_high/low).
- The chart endpoint runs a fresh ccxt OHLCV fetch on every request (no caching). At Coinbase public-rate-limited 1 RPS-ish that's fine for a Board-only dashboard, but if traffic ever grows we should add a short TTL cache (60s would line up with the JS refresh interval).
- `setMarkers` on the candle series is the chosen current-bar highlight mechanism — Lightweight Charts v4 has no native vertical line at a time. The "now" circle + last-close horizontal price line together give the visual anchor.

**Verification:**

- Pre-restart PID 167181 → post-restart 170308.
- All 5 files md5 round-trip MATCH after LF-normalization (Windows working copy carried CRLF; LF-normalized in-place on prod to keep convention).
- `CoinbaseBTCDonchianAgent reloaded: enabled=True auto_execute=False entry=20 exit=6 trend_filter=168 granularity=21600` post-restart — config preserved.
- `CoinbaseBTCDonchianAgent: restored state=cash cost_basis=None last_bar=2026-05-09 06:00:00+00:00` — DB persistence survived; the bar evaluated by the 12:02 UTC scheduler tick is reflected.
- `GET /partials/donchian-chart/coinbase_spot`: HTTP 200, 10.3 KB, 1.99s. Returns 50 candles + 50 high/low/sma points + 0 markers + `current_bar_ts: 1778306400` (= 2026-05-09T06:00:00 UTC). Latest values: close $80,315.98, 20-hi $82,814.23, 6-lo $79,520.44 — close < high so still in CASH.
- `GET /division/coinbase_spot`: HTTP 200, 62 KB, 7.5s. Spot-checks: Buying Power tile NOT in HTML, `id="donchian-chart"` container present, `donchian_chart.js` script include present, first decision-log row's ts_short = `05-09 02:00 ET` (bar-open time, NOT the audit-row write time `05-09 08:02 ET`).

**Inert / dormant on current traffic:**

- The `markers` array on the chart payload is empty — the strategy hasn't placed any orders yet (every bar so far has been SKIP). First BUY will land a green up-arrow `belowBar` at the bar-open time of the entering bar. Will be visible end-to-end on the next breakout.
- `donchian_chart.js` is wrapped in a self-running IIFE that no-ops when `#donchian-chart` isn't in the DOM, so it's harmless on other division pages — but the script tag is gated on coinbase_spot to keep the bytes off the wire where unused.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-uicleanup-20260509-utc-pre
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/web/templates/partials/donchian_chart.html \
      trading_corp/web/static/js/donchian_chart.js
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 06:25 UTC — Dashboard timestamps converted to ET

**Commits:** local-only at deploy time (8 files modified; will be batched in the session-wrap commit).
**Triggered by:** Board direction 2026-05-09 — "change all times to eastern timezone." Board reads dashboards from ET; UTC display required mental conversion on every glance.
**Backup:** prod tarball at `/home/azureuser/backups/pre-et-20260509-0625.tar.gz` (54K, 8 files).

**Files deployed (8 modified):**

- `trading_corp/utils/time.py` — added display formatters: `to_et()`, `format_et_short()` ('MM-DD HH:MM ET'), `format_et_hm()` ('HH:MM ET'), `format_et_hms()` ('HH:MM:SS ET'), `format_et_full()` ('YYYY-MM-DD HH:MM ET'). All use the existing `ET = ZoneInfo("America/New_York")` constant; DST handled automatically.
- `trading_corp/web/data.py` — `build_donchian_view`: converted `ts_short` (decision log), `last_bar_short` (state card), `next_bar_short` (state card), `buy_ts_short` / `sell_ts_short` (round-trips tile) to ET via `format_et_short` / `format_et_hm`.
- `trading_corp/web/app.py` — registered new Jinja filters `et_hms`, `et_short`, `et_full` so templates can format datetime objects directly.
- `trading_corp/web/routes.py` — `expires_at.strftime("%Y-%m-%d %H:%M UTC")` → `format_et_full(expires_at)`.
- `trading_corp/web/templates/partials/donchian_log.html` — header `bar (UTC)` → `bar (ET)`; empty-state copy `(00/06/12/18 UTC)` → `(20:00 / 02:00 / 08:00 / 14:00 ET)`; docstring caption updated.
- `trading_corp/web/templates/partials/donchian_state.html` — caption `UTC` removed (ET label is baked into the formatted value via `format_et_short`).
- `trading_corp/web/templates/approvals.html` + `approval_detail.html` — `{{ row.added_at.strftime('%H:%M:%SZ') }}` → `{{ row.added_at | et_hms }}`.

**Storage layer unchanged.** All `audit_event.ts` / `agent_state.updated_ts` / order-status timestamps stay UTC (ISO-8601 with timezone). The conversion is display-layer only — `to_et()` reads any UTC ISO string or naive-assumed-UTC datetime. Round-trips through restart/cache cleanly.

**Verification:**

- Pre-restart PID 164965 → post-restart 167195.
- All 8 files md5-match end-to-end after SCP (LF-normalized).
- `Donchian scheduler online: ... sleeping 20120s until next bar close` post-restart — math: 06:26:39 UTC + 20120s ≈ 12:02:00 UTC = 08:02 ET ✓.
- `CoinbaseBTCDonchianAgent: reconciled to CASH state` — DB persistence survived the restart cleanly.
- Dashboard render checks (curl localhost:8000):
  - Home tile: dial unchanged (no timestamps), `left: 27.3%` needle position preserved.
  - Division detail: column header reads `bar (ET)`, first row's `ts_short` reads `05-09 02:02 ET`. State card "Last decision" + "Next 6h close" both render in ET.

**Pre-existing surface bug surfaced (decision needed before next deploy):**

- The decision-log column header reads `bar (ET)` but the `ts_short` it displays is the **audit-row write time** (bar close + ~2min), not the bar's open time. Was masked under UTC display ("06:02 UTC" is close enough to bar close). Now in ET it reads `05-09 02:02 ET` while the same row's `reason` text references `@ 2026-05-09T00:00:00+00:00` (bar open). Captured as a decision point under the BACKLOG entry "P3 — Coinbase BTC HODL division-detail UI cleanup". Two paths: (a) switch `data.py` to read `payload.bar_ts` instead of `r["ts"]` (~2-line fix; aligns column with reason text); (b) leave the data, change the column header to "evaluated (ET)". Pick before the UI-cleanup deploy lands.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-et-20260509-0625
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 06:02 UTC — Donchian Phase 2 validation gate ✅ CLOSED

**Not a deploy** — validation milestone. The 02:53 UTC Phase 2 wiring deploy left an open validation gate: "first `donchian_evaluated` audit row should land at ~06:02 UTC." It did, exactly on schedule.

**First bar evaluation (2026-05-09T00:00:00 UTC bar; evaluated 06:02:03 UTC = 02:02:03 EDT):**

- **Decision: SKIP.** `close = $80,374.00 ≤ 20-bar high = $82,814.23` — no breakout, agent stays in CASH (correct given startup state).
- **Channel values:** donchian_low $79,456.00 / current_close $80,374.00 / donchian_high $82,814.23 / trend_filter_sma ~$74,xxx (truncated in audit row but >0 = trend filter passes for future entries).
- **Dial position math verified:** `(80374 - 79456) / (82814.23 - 79456) = 918 / 3358.23 ≈ 0.273` → home-tile needle rendered at `left: 27.3%` ✓.
- **Dedup pointer advanced:** `agent_state` row `last_bar_ts: 2026-05-09T00:00:00+00:00` (the bar that just closed).
- **Scheduler armed for next bar:** `sleeping 21596s` post-evaluation → next wake ~12:02 UTC.

**End-to-end Phase 2 deploy is fully validated.** All UI surfaces operating against real production data:
- Home tile placeholder gone, dial proper rendering with channel values + state-aware edge marker.
- Division-detail decision-log tile populated with row 1.
- agent_state persistence + broker-snapshot reconcile working across the restart cycle.

---

## 2026-05-09 04:26 UTC — Donchian decision-log empty-state copy refresh

**Commits:** `9de5902` (committed before deploy).
**Triggered by:** Board flag during the wait-for-validation window — Phase 1 partial said "strategy not yet wired into the orchestrator," cosmetically stale after Phase 2 shipped.
**Mechanism:** template-only, deployed via `tr -d '\r' | ssh ... 'cat > target'` stdin pipe. **No service restart** — Jinja autoreloaded the template on the next request. Useful precedent: pure-template changes on prod don't require the 30-90s Fidelity-login restart cycle.

**Files deployed (1 modified):**

- `trading_corp/web/templates/partials/donchian_log.html` — empty-state copy: "strategy not yet wired into the orchestrator" → "No decisions logged yet — first row lands at the next 6h-bar close (00/06/12/18 UTC)". Top-of-file docstring updated to describe the orchestrator's per-bar write contract. (Note: ET update later in same session further refined the copy to ET-formatted boundaries.)

**Backup:** prod copy at `/home/azureuser/backups/donchian_log.html.pre-copy-fix-20260509-0426.bak` (separate file, not a tarball — the stdin-pipe deploy used a single-file backup).

**Verification:** `curl localhost:8000/division/coinbase_spot | grep "No decisions"` returned the new copy on the next request, confirming Jinja autoreload.

---

## 2026-05-09 03:40 UTC — Coinbase BTC HODL rename + revert intent to aggressive

**Commits:** local-only at deploy time.
**Triggered by:** Board reaction to the 03:30 UTC deploy — wanted the tile back in the CRYPTO group (alongside Coinbase Futures + BitUnix Futures) and the name updated to `Coinbase BTC HODL` (broker-prefixed pattern).
**Backup:** prod copy of `divisions.yaml` saved to `/home/azureuser/backups/divisions.yaml.pre-rename-20260509-0339.bak` (5.1K).

**Files deployed (1 modified):**

- `config/divisions.yaml` — `coinbase_spot`:
  - `name: Bitcoin HODL` → `Coinbase BTC HODL`.
  - `intent: retirement` → `aggressive`. Tile moves back from Retirement → Crypto group on the home page (since `classify_investment_type` falls through to the broker-rule when intent is not retirement; `coinbase` is in `_CRYPTO_BROKERS`).
  - Comments removed (the prior "retirement-aligned" rationale block is no longer accurate).
  - `target_annual_return: 0.40` unchanged — still consistent with `aggressive` intent.

**Verification:**

- Pre-restart PID 164009 → post-restart 164965.
- md5 round-trip MATCH on `divisions.yaml`.
- `Web command center listening on http://0.0.0.0:8000` + `Donchian scheduler online: ... enabled=True` post-restart.
- `GET /` HTTP 200 (~3.1s, 76.5 KB).
- "Coinbase BTC HODL" appears 1×, "Bitcoin HODL" 0× — clean rename.
- Group section order on home page: `Individual` → `Crypto` → `Retirement`. Coinbase BTC HODL is now in `Crypto`.
- Tile badges: `aggressive` (loss/red), `online` (gain/green), `○ CASH` (edge/gray) — Donchian widget code from the 03:30 deploy is unchanged, badge + dial scaffolding remain.

**Inert / dormant on current traffic:**

- Donchian dial proper still pending the first `donchian_evaluated` audit row at ~06:02 UTC (sleep 8731s post-restart, math: 03:40:25 + 8731s ≈ 06:02:00 UTC ✓).

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
cp /home/azureuser/backups/divisions.yaml.pre-rename-20260509-0339.bak \
   /home/azureuser/trading_corp/config/divisions.yaml
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 03:30 UTC — "Bitcoin HODL" rename + retirement reclass + home-tile Donchian widget

**Commits:** local-only at deploy time (4 files modified in working tree, awaiting Board commit decision).
**Triggered by:** Board reaction to the home-page tile post-Phase-2 — flagged that the tile didn't reflect the new strategy. Asked for: (a) CASH/BTC badge on the home tile (originally part of Phase 1 design intent but only built into the division-detail page), (b) v0 "Dial of Donchian" with state-aware geometry, (c) rename `Coinbase Spot` → `Bitcoin HODL`, (d) reclass intent `aggressive` → `retirement`.
**Backup tag:** `pre-donchian-tile-20260509-0328` (tarball at `/home/azureuser/backups/pre-donchian-tile-20260509-0328.tar.gz`, 22K, 4 modified files).

**Files deployed (4 modified, 0 new):**

- `trading_corp/utils/divisions.py` — added `donchian: dict | None = None` field to the `Division` dataclass. Hydrated only for divisions running a Donchian strategy (today: `coinbase_spot`); other divisions stay `None`.
- `trading_corp/web/data.py` — new `_hydrate_donchian_overview(divisions, db_url)` helper invoked from `build_command_center` after `_hydrate_division_metrics`. Reads `agent_state` for the CASH/BTC state + `cost_basis`, then the most recent `audit_event` row of kind `donchian_evaluated` for `current_close` / `donchian_high` / `donchian_low`. Pre-computes a 0..1 dial position (`(close - low) / (high - low)` clamped). Tolerant of missing data — pre-first-eval, state still renders but dial chrome hides.
- `trading_corp/web/templates/home.html` — division-tile additions:
  - **CASH/BTC badge** in the header row alongside the existing intent + status badges. `● BTC` (green) when in BTC, `○ CASH` (gray) when in CASH. Renders only when `d.donchian` is set.
  - **State-aware Donchian dial** below equity: horizontal gradient bar (loss-tinted left → edge-color middle → gain-tinted right), white needle at `dial_position * 100%` width, state-aware "fires here" edge marker (CASH state → green tick at right edge with hover-tooltip "BUY fires when close breaks above the entry-channel high"; BTC state → red tick at left edge with "SELL fires …"). Numeric trio (`low / close / high`) underneath. Shows `awaiting first 6h-bar evaluation` placeholder when state exists but no audit row has landed yet.
- `config/divisions.yaml` — `coinbase_spot`:
  - `name: Coinbase Spot` → `Bitcoin HODL` (per Board pick).
  - `intent: aggressive` → `retirement`. Side effect: `classify_investment_type` checks `intent == "retirement"` BEFORE the crypto-broker rule, so the home tile **moves out of the CRYPTO group into the RETIREMENT group** alongside Robinhood IRA + Fidelity 401(k). Coinbase Futures + BitUnix Futures remain in CRYPTO (their intent is still `aggressive`).
  - `target_annual_return: 0.40` left UNCHANGED (flagged for Board call — 40% reads aggressive for a retirement-classed division).

**Features shipped:**

- **Bitcoin HODL renamed + reclassed.** Home page now shows the division in the Retirement section with a blue `RETIREMENT` badge.
- **CASH/BTC badge live on the home tile.** Currently shows `○ CASH` (the agent's persisted state from the 02:54 UTC startup reconcile).
- **State-aware Donchian dial scaffolded.** Until the first `donchian_evaluated` row lands at ~06:02 UTC, the placeholder reads "awaiting first 6h-bar evaluation". After 06:02 UTC the dial replaces the placeholder automatically (next page load) — no further deploy needed.

**Notable code changes:**

- **`Division.donchian` is the per-tile pivot point.** Today only `coinbase_spot` is populated. If a future second Donchian strategy lands on a different division, the hydration helper needs broadening (currently hardcoded to `coinbase_spot` slug).
- **Dial geometry is single-channel, state-aware labels** (option 2 from the in-session design discussion). Needle position uses the full `[donchian_low, donchian_high]` channel regardless of state; only the "fires here" edge marker swaps sides. Trade-off: visually the same dial whether in CASH or BTC, with one threshold "active" — keeps the at-a-glance signal consistent across state flips.
- **Dial computation lives in Python (`_hydrate_donchian_overview`), not Jinja.** Template stays dumb. Edge cases (degenerate channel where high <= low) handled in Python; template only checks `dial_position is not none`.

**Verification:**

- Pre-restart PID 161969 → post-restart 164009.
- All 4 files md5-match end-to-end after SCP (LF-normalized).
- `Donchian scheduler online: ... sleeping 9116s until next bar close` — math: 03:30:03 UTC + 9116s ≈ 06:02:00 UTC ✓.
- `CoinbaseBTCDonchianAgent: restored state=cash cost_basis=None last_bar=None` — DB persistence survived the restart (state row was written at the 02:54 UTC reconcile + persists to `agent_state`).
- `CoinbaseBTCDonchianAgent: reconciled to CASH state — held=0.00000000 BTC < $1.00 dust threshold` — broker reconcile pass ran clean.
- `GET /` HTTP 200 (~2.8s).
- Home page render check: "Bitcoin HODL" appears once; "Coinbase Spot" appears 0 times. Tile is in the Retirement group section (group order: Individual → Crypto → Retirement; Bitcoin HODL appears after the Crypto section). Badges visible: `retirement` (blue), `online` (green), `○ CASH` (gray). Dial chrome shows the placeholder.
- 25 unit tests pass (risk_gates + coinbase_btc_donchian_agent).

**Inert / dormant on current traffic:**

- **The dial proper (gradient bar + needle + price triplet) is dormant until 06:02 UTC** when the first `donchian_evaluated` audit row lands. The placeholder is the visible state; no JS / refresh needed — next page load post-06:02 will replace it.
- **`target_annual_return: 0.40` is now visually inconsistent with the retirement intent.** No code path consumes this value for risk-gating (retirement-aligned caps come from `intent: retirement` not from this number); it's tile-context-only. Cosmetic, but should be revisited.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-tile-20260509-0328
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
sudo systemctl restart trading-corp
"
```

---

## 2026-05-09 02:53 UTC — Coinbase BTC Donchian Phase 2 (live wiring + paper-mode deploy)

**Commits:** `a606685` (Phase 2 wiring), preceded by Phase 1 commits `072a484` / `0eb7692` / `fe1cee8` / `f9277e9` — none of the Phase 1 commits had been deployed prior, so this deploy ships Phase 1 + Phase 2 together.
**Triggered by:** Board pickup of the BACKLOG.md "🟡 ACTIVE — Coinbase BTC Donchian (Phase 2 wiring + paper-mode deploy)" brief. coinbase_spot pivots from the Otter+Cypher confluence experiment (no walk-forward edge) to a single 100%-in/out Donchian Channel Breakout strategy (24mo backtest +25.89% alpha vs HODL; 8/10 walk-forward OOS configs beat HODL).
**Backup tag:** `pre-donchian-phase2-20260509-0252` (local git tag on `85d6a80`, the pickup-brief commit). Prod backup as tarball at `/home/azureuser/backups/pre-donchian-phase2-20260509-0252.tar.gz` (48K, 6 modified files).

**Files deployed (11 = 6 modified + 5 net-new):**

- `trading_corp/agents/risk.py` (modified) — section 4 (account max-DD) wrapped in `if not bool(params.get("max_drawdown_disabled", False))` guard. Default-safe; opt-in only.
- `trading_corp/agents/strategies/coinbase_btc_donchian_agent.py` (NEW on prod; locally extended) — agent class from Phase 1 commit fe1cee8 plus a small `last_verdict` attribute exposed for the orchestrator's audit-row write (so SKIP decisions also get the channel highs/lows logged, not just BUY/SELL via order extras).
- `trading_corp/agents/strategies/donchian_btc.py` (NEW on prod) — pure-function decision module from Phase 1 commit 072a484. Both backtest harness and live agent import this same module.
- `trading_corp/main.py` (modified) — construct `CoinbaseBTCDonchianAgent` at startup, reconcile state from `coinbase_spot` snapshot post-`connect_all`, spawn `_scheduled_donchian_loop` alongside the PMCC scheduler. New helpers `_seconds_until_next_6h_boundary` (00/06/12/18 UTC + 2min buffer) + `_fetch_recent_btc_6h_bars` (public ccxt; drops in-progress bar) + `_run_donchian_bar` (one cycle, extracted for ad-hoc trigger). Cancels `donchian_task` cleanly on shutdown.
- `config/risk.yaml` (modified) — `overrides.coinbase_btc_donchian` block: `per_trade_risk_pct=1.0` (full sleeve), `per_strategy_daily_loss_pct=1.0` (effectively-disabled — risk.py reads via `float()` so literal `null` would raise), `max_drawdown_disabled=true`.
- `config/strategies.yaml` (modified) — `lord_otter.enabled` and `market_cypher.enabled` flipped to `false` (paused per 2026-05-08 vision direction; files preserved for future BitUnix Futures wiring); `coinbase_btc_donchian.enabled` flipped to `true`. `auto_execute=false` everywhere.
- `trading_corp/web/data.py` (modified) — `build_donchian_view` from Phase 1 commit f9277e9 (state card data, per-bar decision-log query, realized round-trip pairing).
- `trading_corp/web/templates/division.html` (modified) — donchian tile includes for the `coinbase_spot` division page.
- `trading_corp/web/templates/partials/donchian_state.html` (NEW)
- `trading_corp/web/templates/partials/donchian_log.html` (NEW)
- `trading_corp/web/templates/partials/donchian_trades.html` (NEW)

**Local-only (NOT deployed):**

- `tests/test_risk_gates.py` — new `test_max_drawdown_disabled_flag_skips_cap` locks in default-safe + opt-out semantics for the new flag. Existing `test_max_drawdown_triggers_flatten` already covers the default-on path.

**Features shipped:**

- **Coinbase BTC Donchian goes live in paper mode.** Agent module + locked config (`entry=20, exit=6, trend_filter=168, granularity=21600`) + 6h-bar-close scheduler + risk overrides + UI tiles all on prod. `auto_execute: false` — every BUY/SELL routes through HITL via the web app.
- **`max_drawdown_disabled` per-strategy opt-out for the account-level 15% auto-flatten** — first user is Donchian (24mo backtest max DD 16.49% would have force-flattened the strategy mid-run). Default-safe; no other strategy is opted in.
- **`donchian_evaluated` audit kind starts landing on every 6h-bar boundary**, regardless of decision. The `coinbase_spot` division page's per-bar decision-log tile is its only consumer today.
- **Otter and Cypher disabled on `coinbase_spot`.** Webhook endpoints still accept POSTs (web/webhooks.py is unchanged) but the agents short-circuit on `enabled: false` before ProposedOrder construction. Files preserved per `trading_corp_bitunix_vision.md` — Otter+Cypher ultimately move to BitUnix futures.

**Notable code changes:**

- **`agents/risk.py` section 4 is now opt-out-able per strategy.** This is the only safety-adjacent edit in this deploy; new flag defaults to `False` so existing strategies (PMCC, lord_otter override, manual_coinbase_spot, etc.) are unchanged. Reviewers / future-Claude: the gate's wrapper guards both the `params.get(...)` cap read AND the verdict construction. Don't unwrap one without the other.
- **`coinbase_btc_donchian_agent.py:_last_verdict` is the orchestrator-write hook.** `on_bar_close` short-circuits BEFORE `evaluate_donchian` for `disabled` / `no-bars` / dedup cases — `last_verdict` is only refreshed when the decision module ran, so the orchestrator's `if new_verdict is not None and new_verdict is not prev_verdict` check correctly skips audit writes for short-circuit paths.
- **`_scheduled_donchian_loop` uses ccxt's PUBLIC endpoint for OHLCV** (no auth), same pattern as `paper_trade_replay._default_ccxt_fetcher`. The Coinbase broker's authenticated client (`_exchange.fetch_ohlcv`) was deliberately NOT used — keeps ohlcv read decoupled from broker-auth lifecycle, and the public endpoint has no rate-limit pressure for one call/6h.
- **Bar-boundary math (`_seconds_until_next_6h_boundary`) finds the *strict-greater-than-now* next boundary** — guarantees no double-fire if the loop wakes exactly on a boundary. Combined with the agent's internal `last_bar_ts` dedup, double-fires are double-prevented.
- **On a paper or live "filled" status, the orchestrator calls `agent.mark_filled(side, fill_price=order.limit_price)`.** `limit_price` is the bar-close price the agent used to size the order (set inside `on_bar_close`). For paper-execute fills this is exact; for live fills it's an approximation (real fill price comes from the FillEvent — currently not threaded back to the agent because `_run_order` returns only the status string). Acceptable for Phase 2 paper-mode; revisit if/when `auto_execute` flips.
- **The decision-log tile's empty-state copy says "strategy not yet wired into the orchestrator" — cosmetically stale post-deploy.** Tile was scaffolded in Phase 1 (commit f9277e9) for the pre-wiring state. Will read correct once the first audit row lands at 06:02 UTC. Worth a one-line copy fix on a future surface pass; not blocking.

**Verification:**

- Pre-restart PID 157638 → post-restart 161955 (PID change confirms restart took).
- All 11 files md5-match end-to-end after SCP (LF-normalized).
- journalctl from 02:53:33 → 02:54:14 UTC, full startup sequence:
  - `RiskAgent reloaded config/risk.yaml` — new override block parses cleanly.
  - `LordOtterAgent reloaded config: enabled=False` — Otter disabled.
  - `MarketCypherAgent reloaded config: enabled=False` — Cypher disabled.
  - `CoinbaseBTCDonchianAgent reloaded: enabled=True auto_execute=False entry=20 exit=6 trend_filter=168 granularity=21600` — Donchian config loads with the locked params.
  - `CoinbaseBTCDonchianAgent: no persisted state; defaulting to CASH` — first-boot clean.
  - `CoinbaseBroker(spot) connected (markets_loaded=True)` — Coinbase live (real-read, paper-execute wraps).
  - `CoinbaseBTCDonchianAgent: reconciled to CASH state — held=0.00000000 BTC < $1.00 dust threshold` — broker reconcile snippet ran successfully.
  - `Web command center listening on http://0.0.0.0:8000`.
  - `PMCC scan scheduler online: weekdays 08:30–09:25 ET` — existing scheduler intact.
  - `Donchian scheduler online: wakes at 00/06/12/18 UTC + ~2min (strategy enabled=True, auto_execute=False)`.
  - `Donchian scheduler: sleeping 11266s until next bar close` — math: 11266s ≈ 3h 8m from 02:54 UTC → wakes at 06:02:00 UTC ✓.
- Dashboard smoke (localhost:8000, auth-bypass): `GET /division/coinbase_spot` HTTP 200, 61.7KB. State card renders `○ CASH`, `BTC/USD`, `entry: 20-bar high`, `exit: 6-bar low`, `SMA(168)`, `6h bars`. Per-bar log tile + round-trips tile both render with correct empty states.
- Pre-existing errors only — Fidelity bot-block (paper-fallback to data_exec).

**Inert / dormant on current traffic:**

- ~~**First `donchian_evaluated` audit row will land at ~06:02 UTC 2026-05-09**~~ → **✅ CLOSED 2026-05-09 06:02:03 UTC.** First bar evaluated SKIP (close $80,374 ≤ 20-bar high $82,814.23 — stay in CASH). See the dedicated "06:02 UTC validation gate" entry above for full details.
- **Lord Otter / Market Cypher webhook endpoints (`/webhook/tradingview/lord-otter` and `.../market-cypher`) still accept POSTs.** Agents short-circuit on `enabled: false` before order construction; the audit trail still records `webhook_received` / `alert_ignored`. No Telegram pushes will fire from these strategies.

**Rollback recipe:**

```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-donchian-phase2-20260509-0252
BASE=/home/azureuser/trading_corp
cd \$BASE
tar xzf /home/azureuser/backups/\${TAG}.tar.gz
rm -f trading_corp/agents/strategies/coinbase_btc_donchian_agent.py \
      trading_corp/agents/strategies/donchian_btc.py \
      trading_corp/web/templates/partials/donchian_state.html \
      trading_corp/web/templates/partials/donchian_log.html \
      trading_corp/web/templates/partials/donchian_trades.html
sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 22:04 UTC — Telegram inline-keyboard removed in notification-only mode

**Commits:** local-only (uncommitted at deploy time). Local working tree of `comms/telegram_bot.py` diverges from HEAD by ~133 net lines that match prod's pre-edit content — the file has prod-only changes that were never backported to git (see § Notable code changes below).
**Triggered by:** Direct Board observation during 21:39 UTC `/scan` smoke (see preceding deploy entries today). Slim Telegram pings landed correctly but still rendered Approve/Reject inline-keyboard buttons. CLAUDE.md §HITL surface direction is unambiguous: "Telegram messages do not carry order detail, do not accept Approve/Reject replies, **do not run inline keyboards**." The 2026-05-05 B.4 entry retained the keyboard as a "belt-and-suspenders fallback"; Board called the rule, dropped the fallback.
**Backup tag:** `.pre-telegram-no-keyboard-fix-20260508-2204` (one prod file). Pre-deploy md5 `aa749ac1d7ca9bebef78196688b33ef6`.

**Files deployed (1 modified):**

- `trading_corp/comms/telegram_bot.py` — `_build_approval_message`:
  - Notification-only branch now returns `(text, None)` for the markup (was `(text, kb)`).
  - Slim body trailer line `_Tap Approve / Reject below, or open the dashboard link._` removed (would have been misleading without keys).
  - kb construction moved INSIDE the rich-mode `else` branch — no `InlineKeyboardMarkup` is built when `notification_only=True`.
  - Docstring updated: return type is now `(text, InlineKeyboardMarkup | None)`.
  - Rich-mode (legacy `notification_only=False`) path unchanged byte-for-byte.

**Local-only (NOT deployed):**

- `tests/test_slim_approval_notification.py` — 2 new tests: `test_telegram_notification_only_omits_inline_keyboard` (regression: notification-only must return None for kb, body must NOT mention Approve/Reject) + `test_telegram_rich_mode_keeps_inline_keyboard` (pin: rich mode still produces a keyboard).

**Features shipped (load-bearing for future "is X done?" checks):**

- **Telegram is now truly one-way in production.** Slim notification body + deeplink only. No keyboard, no Approve/Reject buttons, no in-Telegram decision surface. `https://trading.jacksumner.com/approvals/{order_id}` is the sole approval surface.
- **HITL-in-app direction reaches its terminal phase pre-Phase E.** B.4 (2026-05-05) made the slim format the live default and dropped the rich body from Telegram. This deploy drops the keyboard. Phase E (PWA + web push) would let Telegram be dropped entirely; until then Telegram is one-way notification.
- **Test pin in place** so a future refactor doesn't re-introduce the keyboard. The test asserts both `kb is None` AND that the body lacks "Approve"/"Reject" tap-prompt text.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **`comms/telegram_bot.py` had 128 lines of prod-only content not in local HEAD before this deploy.** Pre-deploy md5 diff: local HEAD `3cc9faa2...` vs prod `aa749ac1...` (post-LF-normalization), 538 lines on prod vs 410 in HEAD. The patch was applied directly onto prod's content (download → patch → re-upload) rather than via git, to avoid stomping on prod-only changes. Same pattern as today's earlier `approval_format.py` and `pmcc_robinhood.py` deploys. **Backporting this drift into git is a separate cleanup task.**
- **`_on_callback` handler is unchanged** — it still routes inline-keyboard callbacks for the rich-mode path. So nothing broke for non-notification-only callers (tests, CLI dev). Removing the keyboard handler entirely is a future cleanup, not in scope.
- **Slim body trailer text was removed entirely**, not just edited. Previous text: `_Tap Approve / Reject below, or open the dashboard link._` had two underscores (the italic markers) — keeping it would have left a Markdown-italic span hanging if combined with future format changes. Cleaner to drop.
- **Telegram message length shrinks ~30%** — the trailer was the longest single line in the slim body.

**Verification:**

- Pre-deploy: PID 155725 (post-21:34 restart), Telegram smoke at 21:39 UTC delivered cleanly but with buttons.
- Post-deploy: PID 157624. Port 8000 listening within 60s. `PMCC scan scheduler online` line emitted clean.
- Smoke at 22:08–22:10 UTC: `/scan` triggered. Universe correct (`['ASTS', 'BLSH', 'BULL', 'CIFR', 'HOOD', 'IREN', 'MARA', 'MSTR', 'OPEN', 'RIOT', 'RKLB', 'SMR', 'TSLA']`), 16 orders proposed, 3 ASTS pending_approval_added rows landed. Board confirmed in chat: "yes and yes" — pings landed, no keyboard.
- Zero `notifier 'TelegramChannel._notify_approval' failed` in journalctl since restart. Zero `Can't parse entities`.
- Pre-existing errors only — Fidelity Akamai bot-block.

**Inert / dormant on current traffic:**

- Rich-mode path is dormant on prod (notification_only=true is set on the systemd unit). The rich-mode keyboard code stays in the binary but is never exercised on prod. Future cleanup item.
- 16 approval rows from this scan auto-expire at registry timeout (~3600s) since the prior scan's queued approvals were lost on restart per Board direction (option 3: "restart now, lose pending"). Monday 2026-05-11 12:30 UTC auto-scan re-fires fresh approvals on Monday-open conditions — those are the first non-test exercise of this body.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-telegram-no-keyboard-fix-20260508-2204
  BASE=/home/azureuser/trading_corp/trading_corp/comms
  mv \$BASE/telegram_bot.py.\$TAG \$BASE/telegram_bot.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 21:34 UTC — Telegram parse-error fix (underscore in division slug)

**Commits:** local-only (uncommitted at deploy time). Local HEAD baseline `c057ba80...` was 396 lines; prod was 452 lines (56 lines of prod-only content not backported to git — same pattern as today's other deploys). Patch applied directly to prod's content.
**Triggered by:** First non-zero PMCC scan in 5 days (see preceding 21:08 UTC deploy entry) surfaced a latent bug — every approval ping failed with `Can't parse entities: can't find end of the entity starting at byte offset 26X`. Pre-2026-05-08 the bug never fired because no orders were proposed. Same error was logged once on 2026-05-01 12:33:35 UTC (the only scan that produced output between the earliest entries and today's deploy), but went uninvestigated until volume surfaced it.
**Backup tag:** `.pre-telegram-underscore-fix-20260508-2134`. Pre-deploy md5 `5d1390e92f6297547cb0a7f8bc428557`.

**Files deployed (1 modified):**

- `trading_corp/comms/approval_format.py` — `format_slim_approval_notification`:
  - Symbol slot: `headline_parts.append(sym)` → `headline_parts.append(f"`{sym}`")`. Backtick-wrapped.
  - Division slot: `headline_parts.append(division)` → `headline_parts.append(f"`{division}`")`. Backtick-wrapped.
  - Result: `🎲 *Approval needed*\nROLL SHORT · `MSTR` · `robinhood_pmcc`\n\n[Review on dashboard →](...)` instead of bare `· MSTR · robinhood_pmcc`.

**Local-only (NOT deployed):**

- `tests/test_slim_approval_notification.py` — `test_slim_format_safe_for_legacy_markdown_parse_mode` rewritten as a real regression test. Old assertion was `assert "_" not in headline or "robinhood_pmcc" in headline` (lenient — passed for the buggy state). New assertions: (1) `` `robinhood_pmcc` `` substring required (asserts backtick-wrap), (2) any `_` outside backtick spans must NOT appear in the slim body, with regex stripping of `` `...` `` before counting (since `_` inside a backtick code span is parsed literally by Telegram).

**Features shipped (load-bearing for future "is X done?" checks):**

- **Slim Telegram approval pings now deliver successfully.** Pre-fix: `Can't parse entities` on every ping. Post-fix: zero parse errors observed across two `/scan` smokes (21:39 UTC and 22:08 UTC).
- **The slim format is now Telegram-Markdown-self-contained.** Backtick-wrapping `sym` + `division` makes the headline robust to any underscore-bearing identifier (tickers like `BRK_B` would also work). Combined with the no-keyboard change in the 22:04 UTC entry, the slim body is deeplink-only, parse-error-proof.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **The bug:** `robinhood_pmcc` has one underscore. Pre-fix the slim body had odd total `_` count (1 from division + 2 from the trailer line `_Tap Approve.../link._` = 3). Telegram legacy Markdown reads odd `_` as unmatched italic and rejects the message. Backtick-wrapping `division` makes its `_` literal (inside a code span), bringing the unwrapped `_` count to 0 from the slim body; combined with the 22:04 UTC trailer removal, the count is now 0 outright.
- **The rich format `format_approval_message` ALSO has the same bug** if it were ever sent on prod — the rich header puts `· {division}` bare. It's dead code on prod today (TELEGRAM_NOTIFICATION_ONLY=true, see 2026-05-05 entry) but worth flagging if someone re-enables rich mode.
- **`approval_format.py` had 56 lines of prod-only content not in local HEAD before this deploy.** Same drift pattern as `pmcc_robinhood.py` (681 lines) and `telegram_bot.py` (128 lines). Patch applied directly onto prod's content rather than via git.

**Latent bugs caught + fixed:**

- **Slim Telegram parse-error on every approval** — fixed in this deploy. Latent since the slim formatter was added (Phase A, 2026-05-03 02:09 UTC) but never exercised under load until today's PMCC universe fix unblocked the scan.

**Verification:**

- Pre-deploy: PID 153933 (post-21:08 restart), 20 ASTS approvals queued in registry from the 21:21 UTC smoke, ALL of them got `notifier failed: Can't parse entities` lines in journalctl.
- Post-deploy: PID 155725. Port 8000 listening within 60s. Smoke at 21:39 UTC: `PMCCAgent scan complete: 20 order(s) proposed`, 2 ASTS `pending_approval_added` rows, **zero `notifier failed` lines, zero `parse entities` errors**.
- Telegram delivery confirmed by Board in chat — pings landed (with keyboard at this stage; keyboard fix landed in the 22:04 UTC deploy that followed).

**Inert / dormant on current traffic:**

- Rich format still has the latent bug but is dead on prod. Fix-or-forget when removing the rich code path; not in scope.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-telegram-underscore-fix-20260508-2134
  BASE=/home/azureuser/trading_corp/trading_corp/comms
  mv \$BASE/approval_format.py.\$TAG \$BASE/approval_format.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-08 21:08 UTC — PMCC scan universe fix + LLM rate-limit cap (first non-zero scan in 5 days)

**Commits:** local-only (uncommitted at deploy time). Local working tree pre-edit had 681 lines of uncommitted content beyond HEAD that EXACTLY matched prod content (Phase A PMCC prompt-text refinements from 2026-05-03 02:09 UTC, never backported to git). Patch applied to working-tree-equals-prod content; deploy is byte-stable.
**Triggered by:** Board observation that every weekday PMCC scan since 2026-05-04 reported "PMCC scan complete: no actions needed this cycle." despite 13 PMCC legs detected. Investigation found two compounding bugs: (1) crypto-position regression after the 2026-05-01 Robinhood crypto-snapshot deploy, (2) Anthropic API rate-limit on parallel LLM analysis.
**Backup tag:** `.pre-pmcc-universe-fix-20260508-2108`. Pre-deploy md5 `5f2ac5617ed39e249e55afb15a762fdd`.

**Files deployed (1 modified):**

- `trading_corp/agents/divisions/pmcc_robinhood.py` — 5 surgical edits:
  - `get_universe()`: skip positions with `/` in symbol (HODL crypto: `ETH/USD`, `BTC/USD`). These are visible in dashboard equity but are not tradeable as PMCC underlyings.
  - `scan()` `stock_qty` lookup: same `/` filter on the position dict comprehension.
  - `scan()`: `detect_existing_legs()` moved BEFORE the early-return; early-return relaxed to require BOTH empty `universe` AND empty `legs_by_symbol` (was just empty universe).
  - `scan()`: order-construction loop iterates `set(universe) | set(legs_by_symbol.keys())` — defensive layer so a future stock holding alongside legs doesn't again drop the legs.
  - `scan()` + `analyze_portfolio()`: `asyncio.Semaphore(N)` bounds parallel LLM calls. N from `strategies.yaml` `pmcc.llm_concurrency` (default 3). Caps in-flight burst under Anthropic's 30k input-tokens/min org cap on claude-sonnet-4-6.

**Local-only (NOT deployed):**

- `tests/test_pmcc_logic.py` — new regression test `test_universe_skips_hodl_crypto_positions` reproducing the exact prod scenario (ETH/USD stock-position + ASTS/MARA legs → universe is `{ASTS, MARA}`, NOT `{ETH/USD}`).

**Features shipped (load-bearing for future "is X done?" checks):**

- **PMCC scan produces non-zero orders again.** Two `/scan` smokes today: 20 orders @ 21:21 UTC, 16 orders @ 22:10 UTC. Pre-fix: 0 every weekday since 2026-05-04. Same scan path is wired into the daily 12:30-13:25 UTC scheduler — Monday 2026-05-11 will be the first non-test scheduled exercise.
- **HODL crypto is now isolated from PMCC scan logic.** `ETH/USD` (and any future `/USD`-pattern crypto position from Robinhood crypto branch) is treated as portfolio-value only. Visible in dashboard equity, invisible to PMCC universe.
- **LLM rate-limit failure mode is bounded.** Pre-fix: 5 of 13 legs got 429 errors and lost their LLM verdict (from journalctl 2026-05-08 12:32 UTC). Post-fix: zero 429s observed across both `/scan` smokes.
- **Defensive structural fix on the leg-iteration path.** Even after the crypto filter, a future stock holding (e.g. AAPL in Individual alongside the 13 PMCC legs) would have produced the same "leg verdicts dropped" symptom. The union-iteration in the scan loop guards against this — leg management runs unconditionally for every detected leg.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **The bug, in one line:** `get_universe()` detects "stock positions" by absence of options-flagging chars (`" "`, `"#"`). The 2026-05-01 Robinhood crypto-snapshot deploy added `BTC/USD`-style symbols to `RobinhoodBroker.snapshot()` for the Individual account. Those passed the options-filter (no space, no `#`), so `ETH/USD` was treated as a "stock position." Because `symbols` was non-empty, the existing leg-underlyings fallback (line 1786 pre-edit) never ran. The order-construction loop iterated over `['ETH/USD']` only — every detected PMCC leg's LLM verdict was computed and discarded.
- **Regression timeline confirmed via journalctl:** 2026-05-01 12:32:41 UTC last clean scan (universe: long-call underlyings, 20 orders proposed). 2026-05-04 12:37:43 UTC first broken scan (universe: `['ETH/USD']`, 0 orders). Pattern held every weekday until today's fix.
- **`pmcc_robinhood.py` had 681 lines of uncommitted local content matching prod** (Phase A PMCC prompt-text refinements from 2026-05-03 02:09 — COOLDOWN guard prose, NYSE-calendar-aware `_terminal_dte_time_release` description, LEAP Hard Rule promotion NOTE blocks). Working tree was effectively in sync with prod, just not git-committed. md5 mismatched on pre-deploy check due to CRLF (Windows local) vs LF (prod Linux) line endings — once normalized, working tree equaled prod. Deploy file was the LF-normalized working tree with my 5 edits.
- **Rate-limit fix is configurable.** `pmcc.llm_concurrency` in `strategies.yaml` defaults to 3; tunable without a code deploy if Anthropic's org cap changes. Hot-reload happens on the next `_reload()` call (every scan).
- **The 16-vs-20 order count delta between smokes is normal.** LLM verdicts can shift (different "elevated" vs "routine" classifications, different `target_strike` choices) when called minutes apart; deterministic Python guards (terminal_dte, halfway-roll cooldown, LEAP hard rule) provide the floor of expected behavior across re-runs.
- **One PMCC leg is risk-rejected per ASTS roll_leap pair** — the new-LEAP buy ($30.85/sh × 100 = $3085/contract, exceeds $1500 per-trade cap). Expected behavior, not a bug. Visible in audit as `risk_rejected` events. Per-trade cap can be raised in `risk.yaml` if Board wants this leg to flow.

**Latent bugs caught + fixed:**

- **PMCC scan universe regression** (described above). Latent since 2026-05-01; first detected today. Fixed.
- **Telegram parse-error on every slim ping** — surfaced by this deploy (because no orders had been firing pre-fix). Fixed in the immediately-following 21:34 UTC deploy.
- **Telegram inline-keyboard contradicts CLAUDE.md HITL direction** — also surfaced by this deploy. Fixed in the 22:04 UTC deploy.

**Verification:**

- Pre-deploy: PID 136040 (running since 2026-05-05 01:34 UTC B.4 deploy), every weekday scan reporting 0 orders.
- Post-deploy: PID 153919 (153933 xvfb child). Port 8000 listening within 60s. `PMCC scan scheduler online: weekdays 08:30–09:25 ET` line emitted clean.
- Smoke at 21:19 UTC: `PMCCAgent universe from long call underlyings: ['ASTS', 'BLSH', 'BULL', 'CIFR', 'HOOD', 'IREN', 'MARA', 'MSTR', 'OPEN', 'RIOT', 'RKLB', 'SMR', 'TSLA']` (NOT `['ETH/USD']`). All 13 LLM verdicts landed (no 429s). `PMCCAgent scan complete: 20 order(s) proposed`. ASTS roll_leap pair-coalesced via `pmcc_pair_id`.
- Pre-existing errors only — Fidelity Akamai bot-block + yfinance BTC/USD earnings noise. Zero new errors.

**Inert / dormant on current traffic:**

- Daily PMCC scheduler (12:30-13:25 UTC weekday window) is the natural exercise. Today's smokes were Telegram `/scan`-triggered (same code path as the scheduler). Monday 2026-05-11 is the first auto-scheduled exercise post-fix.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
  TAG=pre-pmcc-universe-fix-20260508-2108
  BASE=/home/azureuser/trading_corp/trading_corp/agents/divisions
  mv \$BASE/pmcc_robinhood.py.\$TAG \$BASE/pmcc_robinhood.py
  sudo systemctl restart trading-corp
"
```

---

## 2026-05-05 01:34 UTC — Phase B.4: `TELEGRAM_NOTIFICATION_ONLY=true` flag flip (slim Telegram body live)

**Commits:** n/a (configuration-only change — no code shipped, only systemd drop-in added)
**Triggered by:** Mon 2026-05-04 was the planned validation day for the B.4 flip (see deploy_log entry 2026-05-03 05:07 UTC and the prior B.1/B.2/B.3 entries). The original gate was "Mon's first PMCC scan validates the web flow with a real Board-routed approval"; the scan ran clean at 12:38:04 UTC but emitted zero approvals (`scheduled_scan_done` payload: "PMCC scan complete: no actions needed this cycle."), so no live web-flow exercise occurred. User chose to flip anyway on the fallback rationale: paper-mode + Telegram inline-keyboard fallback bind real-money risk to zero, the web flow is verified by tests + B.5 manual smoke, and the value of waiting drops with each empty scan.
**Backup tag:** `n/a` for the override file (newly created — no pre-version exists). Pre-flip Environment snapshot for rollback context: `Environment=KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/ PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PATH=/home/azureuser/trading_corp/venv/bin:...`.

**Files deployed (1 new on prod VM, no repo files):**

- `/etc/systemd/system/trading-corp.service.d/override.conf` (NEW on VM, not tracked in repo per CLAUDE.md §6 "VM-side configuration is no-edit from this repo"):
  ```
  [Service]
  Environment=TELEGRAM_NOTIFICATION_ONLY=true
  ```

**Features shipped (load-bearing for future "is X done?" checks):**

- **`TELEGRAM_NOTIFICATION_ONLY=true` is now the live prod default.** The slim Telegram approval body (`🎲 Approval needed · <action> · <symbol> · <division>` + deeplink to `https://trading.jacksumner.com/approvals/{order_id}`) replaces the rich `format_approval_message` body that had been emitting since Phase A's dormant Phase. First real or synthetic approval after this deploy will arrive in slim format.
- **HITL-in-app direction is now FULLY LIVE end-to-end.** Phase A (slim formatter + dormant flag, 2026-05-03 02:09) + Phase B.1 (registry + routes, 03:50) + Phase B.2/B.3 (rich rendering + Modify form + paired coalescing, 04:20) + Phase B.5 (quick-modify presets + new_limit_price, 05:07) + Phase B.4 (this entry, flag flip) means the Board now sees: short Telegram ping → tap deeplink → web-app approval card with structured trade legs / position context / risk / paired coalescing / quick-modify presets → POST resolves the LangGraph interrupt. Telegram inline keyboard remains as a belt-and-suspenders fallback (resolves the same `PendingApprovalRegistry`).
- **Phase E (web push) remains the only deferred phase.** PWA + service worker + push subscription would let Telegram be dropped entirely; not yet scoped.

**Notable code changes (callouts a future Claude shouldn't miss):**

- **No code change in this deploy.** This is purely a systemd Environment add. The producing code path was already in place and dormant (`_notification_only` switch on `TelegramChannel`, plumbed through `main.py:539` from `os.getenv("TELEGRAM_NOTIFICATION_ONLY", "false").lower() == "true"`).
- **VM-side configuration was edited.** Per CLAUDE.md §6, VM systemd unit configuration is "no-edit from this repo" — it lives only on the VM. The override.conf is at `/etc/systemd/system/trading-corp.service.d/override.conf` and is not tracked in the repo. Future maintenance: if more env vars are added, append to this same drop-in (or use a separate `.conf` file in the same drop-in dir; systemd merges them).
- **`DASHBOARD_BASE_URL` was NOT set.** The `comms/approval_format.py` module has `DEFAULT_DASHBOARD_BASE_URL = "https://trading.jacksumner.com"` as the default, and the slim formatter falls through to it when `os.getenv("DASHBOARD_BASE_URL")` is unset. Confirmed via `main.py:541`.
- **Mon's PMCC scan emitted zero approvals.** The 13 detected legs (RKLB, OPEN, MSTR, MARA, CIFR, TSLA, BULL, BLSH, HOOD, RIOT, ASTS, SMR, IREN) had no fires-this-cycle on roll/open conditions. MSTR had a `no liquid weekly contracts` warning (normal — gated out at the liquidity filter). One unrelated `risk_rejected` event at 14:03:24 UTC was a manual MSTR `via=web_button` click, killed at the per-trade cap because $971.65 < $1865 = 1 contract @ $18.65. Did NOT exercise the `/approvals` web flow.

**Verification:**
- Pre-flip: PID 130241, no drop-in dir.
- Post-flip: drop-in `[Service]\nEnvironment=TELEGRAM_NOTIFICATION_ONLY=true` written, `daemon-reload` clean, restart clean.
- New PID 136026 (parent) → 136040 (xvfb-run python child, the actual server).
- `systemctl show -p Environment trading-corp` includes `TELEGRAM_NOTIFICATION_ONLY=true`.
- `/proc/136040/environ` confirms the live python process inherited `TELEGRAM_NOTIFICATION_ONLY=true` (i.e. it's not just on the unit, it's on the running process).
- `ss -tlnp` shows port 8000 listening on PID 136040.
- Dashboard external probe: `GET /` → HTTP 302 in 0.19s (Authelia redirect, normal).
- Post-restart journalctl: only pre-existing errors observed — Fidelity bot-block (Azure VM IP / Akamai layer, documented sharp edge) + yfinance BTC/USD earnings noise. ZERO new errors.

**Inert / dormant on current traffic:**
- Slim format hasn't sent yet at deploy time — first real approval after deploy will be the first observable check. PMCC scan ran clean today; no scout is scheduled until tomorrow's market-open scan. A test-only synthetic alert via the `/webhook/tradingview/lord-otter` endpoint with paper-mode + auto_execute=false would land in `would_have_placed`, not in the approvals registry, so it's NOT a way to smoke the slim format.
- The old rich `format_approval_message` code path remains in the binary (now dead on this prod process). Removing it is a future cleanup, not in scope.

**Rollback recipe:**
```bash
# Run from any host with az CLI logged into Azure subscription 6f20f2e1-28ec-4857-857c-457c7f5212ca
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript \
  --scripts "sudo rm /etc/systemd/system/trading-corp.service.d/override.conf && \
             sudo rmdir /etc/systemd/system/trading-corp.service.d/ && \
             sudo systemctl daemon-reload && \
             sudo systemctl restart trading-corp && \
             systemctl show -p Environment trading-corp"
# Expected: TELEGRAM_NOTIFICATION_ONLY no longer appears; rich Telegram body resumes.
```

---

## 2026-05-03 05:07 UTC — Phase B.5: quick-modify presets + new_limit_price + graph routing fix

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Continuation of the same Sunday HITL session that shipped B.1 (03:50), B.2+B.3 (04:20). User chose to ship B.5 today and plan B.4 (slim-flag flip) for tomorrow once Mon's PMCC scan validates the web flow live. B.5 = the "quick-modify ±½ size + limit ±5%" preset buttons from `planning/hitl_in_app_design.md` §14, plus the underlying `new_limit_price` plumbing through BoardDecision / graph / POST handler. Fixed a latent graph-routing bug in modify_then_risk_node along the way (unconditional edge back to risk overwrote final_status — manifested as "modify with no fields silently re-pauses at approval forever"; now routes to end_rejected when modify_then_risk_node bails).
**Backup tag:** `.pre-b5-quick-modify-20260503-0505` (on the 5 mutated files)

**Files deployed (5 modified):**

- `trading_corp/graph/interrupts.py`:
  - `BoardDecision` gains `new_limit_price: float | None = None` field. Documented as "only used when decision='modify'", parallel to existing `new_qty`.
  - `request_board_approval` decodes `new_limit_price` from the resume payload alongside `new_qty`.
- `trading_corp/graph/ceo_graph.py`:
  - `approval_node` stashes `new_limit_price` on `state["board_decision"]` (was missing — modify_then_risk_node couldn't see it without this).
  - `modify_then_risk_node` accepts BOTH new_qty and new_limit_price (either alone or both together). Validates each: > 0 when supplied; rejects with `final_status='board_rejected'` when neither field is supplied OR when a supplied field is non-positive. Builds a `board-modified (qty=X, limit=$Y)` rationale annotation showing exactly what changed.
  - **NEW conditional edge** `modify_then_risk_route`: if `final_status == 'board_rejected'` → end_rejected, else → risk. Replaces the unconditional `g.add_edge("modify_then_risk", "risk")` that was silently overwriting final_status by re-running risk on the unmodified order. Without this, the empty-modify path became an infinite re-pause loop. Pinned by new test `test_modify_with_no_fields_rejects`.
- `trading_corp/main.py`:
  - `_run_order` resume payload now includes `new_limit_price` so it survives the LangGraph round-trip.
- `trading_corp/web/routes.py`:
  - `POST /approvals/{order_id}/decide` accepts `new_limit_price` in form OR JSON body (parallel to existing `new_qty`). Modify-validation: at least ONE of new_qty / new_limit_price required (400 with "modify requires at least one of new_qty / new_limit_price" if neither). Each supplied field validated independently (numeric, > 0). Response message includes `qty=X, limit=$Y` for the affected fields.
  - Renamed pre-existing test assertion error message from "new_qty is required for decision=modify" to the both-fields message above; old test renamed to `test_decide_modify_missing_both_fields_400`.
- `trading_corp/web/templates/approval_detail.html`:
  - **Quick-modify preset row** added inside the modify-form panel: 4 buttons in a 2×2 (mobile) / 1×4 (desktop) grid — `½× size`, `2× size`, `limit −5%`, `limit +5%`. Each button shows the COMPUTED preset value below the label (e.g. "½× size → 1" for a qty=2 order; "limit −5% → $5.22" for a $5.50 order). Buttons are `type="button"` with `data-preset-kind` + `data-preset-value` attributes; JS handler intercepts click, calls a shared `_submitDecision('modify', {field: value})` helper, and the form posts in one tap.
  - **`new_limit_price` input field** added below the existing custom-qty input — only renders when the order has a non-null limit/mark price (skipped for market orders without a limit price).
  - Limit-direction preset buttons disabled (with explanatory tooltip) when the order has no `mark` price (e.g. market orders).
  - Submit handler refactored: pulled the URL-encode + fetch + result-swap logic out of the form-submit listener into a shared `_submitDecision(decision, extras)` helper used by both preset clicks and the regular form submit. JS form-submit handler now also pulls `new_limit_price` from the form when present (it didn't before — the input field is new).

**Local-only (NOT deployed):**
- `tests/test_approvals_routes.py` extended with 8 new tests: 6 modify-with-new_limit_price cases (only-limit, both-fields-together, neither-field-400 [renamed], zero-limit-400, non-numeric-400, JSON body) + 2 template smoke tests (presets render with computed values, limit-direction buttons disabled when no price).
- `tests/test_graph_hitl.py` extended with 2 new tests: `test_modify_with_new_limit_price_applies_to_fill` (full graph round-trip — limit-only modify re-runs risk and fills at the new price) + `test_modify_with_no_fields_rejects` (regression test for the routing fix above).

**Features shipped (load-bearing for future "is X done?" checks):**
- **One-tap quick-modify is live.** Board sees presets with the actual numeric outcome on each button ("½× size → 1") so there's no math required; tap fires the modify, no second confirmation. Mobile UX optimized.
- **`new_limit_price` is now a first-class modify field.** End-to-end: web POST → BoardDecision → graph state → modify_then_risk_node → re-evaluate risk → re-pause approval at the new price → execute at the new price on approve. Test pin: `test_modify_with_new_limit_price_applies_to_fill` verifies the fill happens at the modified price, not the original.
- **Graph correctness fix on the modify path.** Empty modify (no qty + no limit) now routes cleanly to `end_rejected` instead of silently re-pausing at approval forever. Pre-B.5 this latent bug existed but no test exercised it — discovered while writing B.5 tests.
- **Modify form on the detail page now renders both inputs** — qty AND limit_price (when applicable) — so the Board can hand-edit either independently of the presets.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Limit-direction presets are ±5% from the order's `mark` (or `limit_price`), NOT from the current market price.** Rationale: the user's anchor is what THEY proposed, not what the market is doing right now. If the order proposes $5.50 limit and the market has moved to $5.80, "limit −5%" goes to $5.22 (5% below the proposal), not $5.51 (5% below market). This makes the math predictable from the displayed price; a future "limit toward bid/ask" preset could be added if needed.
- **Quick-modify presets bypass the custom input field entirely.** Each preset POSTs only the relevant field (`new_qty` for size presets, `new_limit_price` for limit presets) so user edits to the custom inputs are NOT inadvertently submitted with a preset. The `reason` field IS auto-populated as `"preset:qty-half"` / `"preset:limit-down"` / etc. so the audit log distinguishes preset use from custom-input use.
- **Paired mode disables the entire Modify path** (B.2 decision preserved). Paired-modify is a future phase — the design needs to think through per-leg vs both-legs semantics. Telegram `/modify <id> <qty>` still works as the per-leg fallback.
- **Graph routing fix is the load-bearing change for correctness.** The new `modify_then_risk_route` conditional edge is the right architectural fix; without it, ANY modify_then_risk_node bail (empty modify, invalid qty, invalid limit) would silently re-pause at approval. The fix is symmetric — handles current AND future bail conditions.
- **`approval_node` was missing `new_limit_price` in the board_decision dict.** Caught during test debugging — the resume payload had it, BoardDecision had it, but approval_node forgot to copy it from BoardDecision to the graph state. Without that, modify_then_risk_node always saw `new_limit_price=None` even when the user supplied one. One-line fix.
- **`_decide` validation is per-field independent.** Both new_qty and new_limit_price get validated separately, so a request with `new_qty=2.5, new_limit_price=invalid` rejects on the limit field's validation rather than silently dropping it. Errors are specific ("new_limit_price must be > 0" vs "new_qty must be > 0").

**Latent bugs caught + fixed:**
- **modify_then_risk routing bug** (described above). Pre-existing since the original Phase 1 graph wiring; first exposed by B.5 tests. Fixed with the new conditional edge.
- **approval_node missed copying new_limit_price** to graph state. Introduced in this same B.5 deploy but caught in the integration tests before shipping.

**Verification:**
- Pre-deploy: 495 unit tests pass on local (vs 485 pre-B.5 baseline; +10 = 8 new in test_approvals_routes + 2 new in test_graph_hitl). 5 pre-existing P2 failures unchanged (BACKLOG line 1247).
- md5 5/5 files MATCH between local and prod post-scp.
- Backup tag `.pre-b5-quick-modify-20260503-0505` placed on the 5 mutated prod files pre-deploy.
- PID 119776 → 121271 (restart at 05:07:13 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- `GET /` 200 in 3.29s; `GET /approvals` 200 in 2.77s.
- `POST /approvals/x/decide` with `decision=modify` (no fields) → 400 with the new B.5 message `"modify requires at least one of new_qty / new_limit_price"` — confirms the validation branch landed.
- `POST /approvals/x/decide` with `decision=modify&new_limit_price=5.50` (no qty) on a non-pending order_id → 409 (not 400) — confirms `new_limit_price` is parsed as a valid modify field, just no entry to resolve. The 409-vs-404 for unknown order_id is pre-B.5 behavior; refining that is a B-x polish item.
- journalctl post-restart: 6 ERROR lines, all pre-existing (5 Fidelity Azure-IP block + 1 yfinance BTC/USD earnings noise). ZERO new errors related to BoardDecision, graph routing, modify form, or POST handler.

**Inert / dormant on current traffic:**
- **Quick-modify presets have no users until the Board takes a Modify action on a real pending approval.** Same condition as B.2 modify — first scout-emitted approval with a Board choice to Modify rather than Approve/Reject. Mon ~13:30 UTC PMCC scan is the first natural exercise.
- **`new_limit_price` graph wiring** is dormant on the approve/reject paths (only fires when decision=modify). Approve / Reject still take the same byte-identical paths as B.4-pre.
- **Phase A flag still NOT flipped.** `TELEGRAM_NOTIFICATION_ONLY` stays unset. B.4 plan: validate web on Mon's first live PMCC approval, then flip same-day. Soak window collapsed from "1 week" to "1 live exercise" since paper mode + inline keyboard fallback bound real-money risk to zero.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b5-quick-modify-20260503-0505; BASE=/home/azureuser/trading_corp;
for f in trading_corp/graph/interrupts.py trading_corp/graph/ceo_graph.py trading_corp/main.py trading_corp/web/routes.py trading_corp/web/templates/approval_detail.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

(Reverts B.5 only — leaves B.1+B.2+B.3 intact.)

---

## 2026-05-03 04:20 UTC — Phase B.2 + B.3: rich `/approvals` rendering + Modify form + paired-roll coalescing

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Continuation of the same Sunday session that shipped B.1 at 03:50 UTC. User chose two-cut strategy ("B.1 alone first, then B.2+B.3 bundled") so the load-bearing registry seam validated discrete on prod before stacking polish + safety-critical pair coalescing on top. B.2 = Tailwind structured rendering of trade legs / position context / risk / warnings (replaces B.1's raw-JSON dump) + inline Modify form. B.3 = the safety-critical pair-coalescing fix: PMCC roll close+open siblings now run in parallel via asyncio.gather, land in registry simultaneously, render as ONE card with combined Net Debit/Credit, ONE Approve button resolves both atomically. Eliminates the original "approve close, reject open → naked short" failure mode that was the entire reason the BACKLOG P0 existed.
**Backup tag:** `.pre-b23-hitl-rich-pairs-20260503-0417` (on the 5 mutated files; the new position_context.py file has no backup target — `rm` is the rollback)

**Files deployed (5 modified, 1 new):**

*New:*
- `trading_corp/comms/position_context.py` — structured-dict builder consumed by the web detail template. `build_approval_view(detail)` returns `{headline, trade.legs[], context, risk, warnings, pmcc_pair_id, raw_extra}`. Each leg carries `{side, qty, asset_class, symbol, action_label, option, mark, bid, ask, gross_dollars, side_sign, rationale}` plus crypto-/stock-specific fields where applicable. `coalesce_paired_view([close_v, open_v])` merges two single-leg views into a paired one with combined `trade.legs[]` + summed `net_dollars`; sorts close-leg-first; uses the close leg as the headline anchor; surfaces `is_paired=True` and `paired_order_ids[]`. Defensive against missing/malformed `extra_json` (decodes JSON; falls back to `extra` dict; safe `_safe_float` wrapper). The Telegram formatter (`comms/approval_format.py`) is UNCHANGED — its existing string-output path remains the source of truth for Telegram message bodies; the structured dict is web-only in B.2.

*Modified:*
- `trading_corp/comms/pending_registry.py`:
  - `PendingEntry` gains `pmcc_pair_id: str | None = None` field (extracted from `req.detail["order"]["extra_json"]` at `wait()` registration time via the new module-level `_extract_pair_id` helper).
  - `pending_approval_added` audit row payload now includes `pmcc_pair_id` (renders in audit trail; lets the dashboard recover paired state from audit if a restart wipes the registry).
  - `resolve(...)` gains `also_resolve_paired: bool = False`. When True AND the entry has a paired sibling currently pending, the sibling's Future is resolved with the SAME decision in the same call. Two `board_decision_received` audit rows emit (one per leg), each tagged `paired_with=<sibling_order_id>` for traceability. Graceful no-op when the paired flag is set but no sibling is in the registry.
  - New `find_sibling(order_id) -> ApprovalRequest | None`: looks up the OTHER pending entry sharing the order's `pmcc_pair_id`. Used by the detail-page handler to render coalesced view at request time.
- `trading_corp/main.py`:
  - New module-level `_group_orders_by_pair_id(orders) -> list[list[ProposedOrder]]` helper. Groups orders so paired siblings (sharing `extra.pmcc_pair_id`) end up in the same sub-list; solo orders become singleton lists. Group ordering preserves the position of the first-seen leg of each pair.
  - PMCC scan loop refactored: instead of `for order in orders: await _run_order(...)`, the loop iterates `groups = _group_orders_by_pair_id(orders)`. Solo groups await sequentially (preserves prior blast-radius bound). Multi-leg groups dispatch via `asyncio.gather(*[_run_order(...) for o in group])` so both ApprovalRequests land in the registry at the same instant — that's what makes the web detail page's coalesced view actually appear (sibling lookup at render time succeeds because both legs are simultaneously pending).
  - Fidelity scan path UNCHANGED — Fidelity is read-only-on-Azure-VM and the autonomous-execution path is deferred (BACKLOG P3 #1341); applying the same refactor would expand blast radius without delivering value today.
- `trading_corp/web/routes.py`:
  - Two new module-level helpers: `_group_index_entries(entries)` collapses paired entries into ONE `kind='paired'` row per pair_id (close leg as anchor, combined headline "ROLL · {SYM} · close + open"); `_summary_action_hint` / `_summary_symbol` parse the rich Telegram-Markdown summary's first line to pick the close-leg anchor + extract symbol for the combined headline.
  - `GET /approvals` reworked: passes `rows = _group_index_entries(entries)` + `total_legs` to the template. Each row carries `{kind, entries, is_paired, primary_order_id, division, summary, added_at, pair_id}`.
  - `GET /approvals/{order_id}` reworked: builds primary view via `build_approval_view`, looks up sibling via `registry.find_sibling`, calls `coalesce_paired_view([primary, sibling])` when sibling exists. Template gets `view`, `is_paired`, `sibling_order_id`. POST target stays the primary order_id (sibling is resolved via `also_resolve_paired` flag).
  - `POST /approvals/{order_id}/decide` extended: accepts `decision="modify"` with required `new_qty` validation (numeric, > 0) — 400 on missing/invalid/zero/negative qty. Accepts `also_resolve_paired` form field (or JSON bool) — when truthy AND sibling pending, both Futures resolve atomically (single POST). Response message includes "(qty=X)" for modify and "· both legs resolved" when paired.
- `trading_corp/web/templates/approvals.html`:
  - Renders `rows[]` instead of `entries[]`. Paired rows get a `paired` badge and the combined headline.
  - Header shows "(N cards · M legs)" when N != M (i.e., paired rolls present).
- `trading_corp/web/templates/approval_detail.html`:
  - Replaced the raw-JSON `<pre>` dump with structured rendering: headline (emoji + action label + symbol + division + paired-roll badge); trade-legs block (each leg: side + qty + option/symbol + dte/delta + mark + bid/ask + signed gross dollars; net row when ≥2 legs); position-context block (LEAP, days held, cost vs mark, P&L pct, unrealized $/%, roll count + prior credit); risk verdict block (color-coded by verdict); warnings block (when present).
  - Inline Modify form expands on click: numeric input + optional reason; submits `decision=modify` to existing POST endpoint. Disabled with explanatory tooltip when the card is paired (paired-modify is B.x; Telegram `/modify <id> <qty>` still works as fallback).
  - Form-submit JS intercepts both Approve/Reject/Modify; URL-encodes form data manually so the also_resolve_paired hidden field travels with paired cards. Result fragment swap stays in-page (mobile UX). 409 → "already decided" warn message; 400 → strips HTML and shows up to 200 chars of detail.
  - Raw-detail JSON kept as a collapsible `<details>` debug block at the bottom.

**Local-only (NOT deployed):**
- `tests/test_position_context_view.py` — 20 new tests pinning the structured dict shape (headline action labels for option/roll/stock/crypto, dollar math sign, bid/ask extraction, leap pnl_pct computation, risk color normalization, pair_id extraction, defensive fallbacks for missing/malformed extra_json, coalesce close-first ordering + net math + singleton pass-through + empty-list raise).
- `tests/test_pair_grouping.py` — 6 tests for `_group_orders_by_pair_id` (solo passthrough, paired grouping, mixed solo+paired, two independent pairs, empty list, group ordering preserves first-leg position).
- `tests/test_pending_registry.py` extended with 7 B.3 tests: pair_id extraction into entry, find_sibling both directions, find_sibling None when solo / no pair_id, also_resolve_paired atomicity (both Futures resolved with same decision), audit row tagging with paired_with on each leg, graceful no-op when no sibling.
- `tests/test_approvals_routes.py` extended with 9 tests: 6 modify-flow tests (success, missing new_qty, zero qty, negative qty, non-numeric qty, JSON body) + 3 paired-flow tests (index coalescing, paired detail rendering with also_resolve_paired hidden field, paired POST resolves sibling, sanity test that omitting flag only resolves one leg).

**Features shipped (load-bearing for future "is X done?" checks):**
- **Web `/approvals/{order_id}` is now decision-quality.** A Board member can read the trade legs, position context (LEAP, prior rolls, P&L), risk verdict, and warnings on a phone screen — same information the Telegram rich body has rendered since 2026-05-02. The page is fully self-contained; tap Approve/Reject/Modify and the LangGraph resumes.
- **Modify on the web works end-to-end** for solo orders. Inline form, qty input, optional reason; submits to the same POST endpoint. graph/ceo_graph's `modify_then_risk_node` re-evaluates risk and re-emits an interrupt with the modified qty — the registry receives a NEW ApprovalRequest under the same order_id, the next Board action lands on the modified version. (Verified by route test; full graph integration relies on existing test_graph_hitl which is unchanged.)
- **Paired-roll coalescing is live.** When a PMCC scan emits a roll (close + open with shared pmcc_pair_id), `_group_orders_by_pair_id` puts both into the same group, asyncio.gather launches them in parallel, both interrupts fire, both ApprovalRequests land in the registry simultaneously. The web `/approvals/{order_id}` detail page renders ONE card with both legs + Net Debit/Credit; ONE Approve click resolves BOTH Futures via `also_resolve_paired=True`. The "approve close, reject open → naked short" failure mode is structurally impossible from the web surface — both legs share the same atomic decision.
- **Telegram inline keyboard still works in parallel** for both solo AND paired orders. The Telegram path resolves one leg at a time per click (legacy behavior unchanged); the web path resolves both legs per paired click. First-decision-wins applies per-leg, so a Telegram-approve-then-web-paired-approve race resolves cleanly (web's already-resolved leg returns False from registry.resolve and skips audit re-write).

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`asyncio.gather` for paired siblings is the load-bearing change.** Without it, sequential processing meant only ONE leg was ever in the registry at a time → web coalescing was meaningless. The change keeps risk-evaluation per-leg (each gets its own thread_id, each runs through risk independently in parallel — both legs are read-only on broker state at risk time, so parallel is safe). If a future strategy emits triplet+ orders sharing a pair_id (currently only PMCC roll = 2-leg compound), the same code paths handle them — `_group_orders_by_pair_id` doesn't cap group size, `coalesce_paired_view` accepts ≥2 views, `find_sibling` returns the FIRST sibling (not all). Triplets would render only 2 legs in the coalesced card; revisit if/when needed.
- **Render-time coalescing means the user can land on either leg's URL and see both.** The detail handler always builds the primary view, then asks the registry for a sibling. If found → coalesced view. If not → solo view. Sibling absence at render time is normal during the brief window between leg 1's interrupt firing and leg 2's interrupt firing — htmx polling on the detail page would close that gap; deferred to a B-v2 polish PR (design §6 v2 note).
- **Modify on paired cards is intentionally disabled in B.2.** The button shows but is disabled with a tooltip pointing the user at Telegram `/modify <id> <qty>` for individual-leg modifications. Modifying a paired roll is conceptually fraught — you'd need separate qty inputs per leg, and re-evaluating risk on the close leg might invalidate the open leg's risk verdict. Defer to a later phase that thinks through the modify-paired contract.
- **`coalesce_paired_view` uses the CLOSE leg's risk verdict as the primary.** Both legs were risk-evaluated independently; surfacing both verdicts in one card is a B-v2 polish task. Today the close leg's verdict is shown — typically "approve" since both legs of a roll usually pass risk. If the OPEN leg was risk-resized but the CLOSE leg approved cleanly, the user wouldn't see the resize on the coalesced card. Mitigation: warnings block surfaces both legs' warnings (deduped). Real-money risk is bounded — the resized qty already happened during risk evaluation, the user's only choice at this stage is approve/reject the post-risk shape.
- **`pmcc_pair_id` extraction lives in the registry, not the route handler.** Decoded once at `wait()` registration, stored on the entry. Route + sibling lookup just read `entry.pmcc_pair_id` — no JSON re-parse per request. Test fixtures need to include the `extra_json` field on the order row to exercise pair-coalescing.
- **B.4 still NOT shipped.** `TELEGRAM_NOTIFICATION_ONLY=true` env stays unset. Soak the parallel paths (rich Telegram + new structured web) for ~1 week before flipping. Until then, both surfaces work; first-decision-wins resolves any race.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 485 unit tests pass on local (vs 442 pre-B.2/B.3 baseline; +43 = 20 view + 7 registry + 6 grouping + 6 routes new modify + 4 routes new paired). 5 pre-existing P2 failures unchanged (BACKLOG line 1247).
- md5 6/6 files MATCH between local and prod post-scp.
- Backup tag `.pre-b23-hitl-rich-pairs-20260503-0417` placed on the 5 mutated prod files pre-deploy.
- PID 118285 → 119776 (restart at 04:20:05 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- `GET /` 200 in 2.96s; `GET /approvals` 200 in 2.74s (renders new "HITL · phase B" badge + empty state); `GET /research` 200 in 2.55s.
- POST `/approvals/x/decide` with invalid decision returns 400 with the new error string `"decision must be 'approve', 'reject', or 'modify'"` — confirms the modify branch landed and the error message updated.
- journalctl post-restart: 6 ERROR lines, all pre-existing — 5 Fidelity Azure-IP block (BACKLOG P1 #1276) + 1 yfinance BTC/USD earnings noise. Same set as B.1 deploy + every restart since Fidelity scope was added. ZERO new errors related to position_context, pair grouping, registry pair semantics, or the modify form.
- Audit log on prod immediately after restart shows `research_position_context_emitted` rows for both lord_otter and market_cypher (the existing position-context prime task runs on startup, unrelated to this deploy — sanity-check that the rest of the system is healthy).

**Inert / dormant on current traffic:**
- **Pair coalescing has no work to do until a PMCC scan emits a roll.** Sun pre-market (deploy time was 04:20 UTC Sunday); next scheduled scan Mon 2026-05-04 ~13:30 UTC. If that scan emits any rolls (LEAP-Hard-Rule promotion fires, halfway-roll cooldown doesn't fire, etc.), they'll be the first production exercise of the parallel-grouping + coalesced-card flow. Solo orders (open_pmcc, sell_weekly) take the unchanged sequential path.
- **Modify form has no users until a Board approval is pending.** Same condition as above — first scout-emitted approval Mon 13:30 UTC.
- **Restart-recovery still NOT wired** (carried forward from B.1 — design §3 / §9 v2). Mid-approval restart wipes the registry; LangGraph state survives in SqliteSaver but the user has no surface to act on it. Acceptable; v2 polish.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b23-hitl-rich-pairs-20260503-0417; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/pending_registry.py trading_corp/main.py trading_corp/web/routes.py trading_corp/web/templates/approvals.html trading_corp/web/templates/approval_detail.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm -f \$BASE/trading_corp/comms/position_context.py;
sudo systemctl restart trading-corp
"
```

(Note: this rollback ALSO needs to back-out B.1's registry seam if you want to fully revert HITL-in-app — see the B.1 rollback at the 03:50 UTC entry. Reverting B.2/B.3 alone leaves B.1's bare-bones `/approvals` page intact, which is a valid stopping point.)

---

## 2026-05-03 03:50 UTC — Phase B.1: HITL `/approvals` web surface + PendingApprovalRegistry seam

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction (BACKLOG P0 — "HITL approval flow lives in the web app", NEW 2026-05-03). Phase A shipped 02:09 UTC tonight (dormant slim-Telegram switch). Phase B.1 builds the foundation the slim flag will eventually point at: a new `PendingApprovalRegistry` that owns the per-order Future, plus three new `/approvals*` routes that read + resolve via that registry. Telegram inline-keyboard path is preserved in parallel (first-decision-wins) so the soak window has both surfaces live. User explicit go-ahead in-session 2026-05-03 ~02:30 UTC; chose two-cut strategy (B.1 separate from B.2+B.3) so the load-bearing registry seam validates discrete on prod before stacking polish/pair-coalescing on top. Markets-closed Sun→Mon window picked deliberately for HITL deploys.
**Backup tag:** `.pre-b1-hitl-web-20260503-0349` (on the 4 mutated files; the 3 new files have no backup target — `rm` is the rollback for them)

**Files deployed (4 modified, 3 new):**

*New:*
- `trading_corp/comms/pending_registry.py` — `PendingApprovalRegistry` class. Public surface: `wait(req, timeout_s)` (orchestrator-side; replaces `channel.request_approval`), `resolve(order_id, decision, source)` (resolver-side; called by Telegram callback OR web POST; first-wins), `register_notifier(fn)` (fan-out hook; TelegramChannel registers its message-send), `list_pending()` / `get(order_id)` / `get_entry(order_id)` (read-only views for the index/detail UIs), `pending_count()`. Audit chain: `pending_approval_added` (written by `wait()` BEFORE notifiers fire so dashboard can recover from audit even if notifiers all fail) → `board_decision_received` (written by `resolve()` with `source` tag — 'telegram'/'web'/'cli'/'auto'/'timeout'). Exception in one notifier doesn't block others (`_safe_notify` wraps each with broad except). Audit writes are best-effort (try/except) so a temporarily-down audit DB doesn't block approvals.
- `trading_corp/web/templates/approvals.html` — index template. Empty state + populated list. Each row: summary, division, order_id (truncated), added_at HH:MM:SSZ, "Review →" link to detail page. Tailwind chrome via `base.html`. Mobile-responsive.
- `trading_corp/web/templates/approval_detail.html` — detail template. Header (division · order_id · added_at), summary block, expandable raw-detail JSON dump (B.2 will replace with structured renderer), Approve / Reject form buttons. In-page JS intercepts the form POST, swaps result into `#decision-result` so the user stays on the page (no full-page reload). 409 → "Already decided" message with warn color. Modify intentionally NOT shipped in B.1 (deferred to B.2 per design §14).

*Modified:*
- `trading_corp/comms/telegram_bot.py` — constructor accepts `registry: PendingApprovalRegistry | None = None`. New `_build_approval_message(req)` factored out of the existing `request_approval` so the slim/rich body logic is shared. New `_notify_approval(req)` is the fan-out hook (registered on registry in `start()` after `_app` is initialized — needs the bot to send messages). `request_approval` now branches: when registry is set, delegates to `await self._registry.wait(req)`; without registry, falls back to the legacy in-channel `_pending` Future flow (preserved verbatim so non-registry paths — CLI dev, older tests — see byte-identical behavior). `_on_callback`, `/approve`, `/reject`, `/modify` all migrated to `_resolve_decision(order_id, decision)` helper that prefers `registry.resolve(..., source="telegram")` then falls back to legacy `_pending`. `_on_status_cmd` reads count from registry when wired. Inline keyboard preserved in slim mode (Phase A behavior unchanged).
- `trading_corp/main.py` — constructs `pending_registry = PendingApprovalRegistry(logger_agent=logger_agent)` immediately after the agent block, before TelegramChannel. Threaded into `TelegramChannel(... registry=pending_registry)` constructor, into `tg_deps = WebDeps(... pending_registry=pending_registry)` (for /pending command surface, future-proofed though not consumed in B.1), and into `_start_web_server(... pending_registry=pending_registry)` which forwards to `WebDeps(... pending_registry=...)`. `_run_order` is UNCHANGED — still calls `await channel.request_approval(req)`; the channel internally delegates to `registry.wait(req)` when wired, so the orchestrator path is byte-identical at the call-site level. This keeps test_graph_hitl.py green without modification.
- `trading_corp/web/app.py` — `WebDeps` dataclass gains `pending_registry: Any = None` field. Doc-commented as "constructed in main.py before TelegramChannel so the channel can register its message-send as a notifier."
- `trading_corp/web/routes.py` — three new routes registered after `/system`:
  - `GET /approvals` — `templates.TemplateResponse("approvals.html", {snap, entries, registry_unavailable})`. Empty state when `entries == []`; explanatory note when registry is None (CLI fallback / dev).
  - `GET /approvals/{order_id}` — fetches `registry.get_entry(order_id)`; 404 when not pending. Renders detail template.
  - `POST /approvals/{order_id}/decide` — accepts JSON or form-encoded body; `decision` in `{approve, reject}` (modify deferred); calls `registry.resolve(order_id, decision, source="web")`. 200 + small HTML fragment on accept; 409 on already-resolved; 400 on bad decision; 404 on no-registry.

**Local-only (NOT deployed):**
- `tests/test_pending_registry.py` — 11 tests covering wait/resolve happy path, idempotency (second resolve returns False), unknown-order-id, audit row writes (`pending_approval_added`, `board_decision_received` with source tag), notifier fan-out + exception isolation, list_pending newest-first ordering, get/get_entry.
- `tests/test_approvals_routes.py` — 13 tests using FastAPI TestClient: index empty/populated/no-registry states, detail 200/404 + raw-detail JSON rendering, decide approve/reject (resolves Future), 409 on duplicate, 400 on invalid/unknown decision, 404 when registry is None, JSON-body acceptance.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`PendingApprovalRegistry` is the new HITL chokepoint.** Single instance per process (constructed in `main.py`). The web app + Telegram both share it; first-decision-wins, second gets a 409 (web) or "already decided" (Telegram). Tests construct their own per case.
- **Three new web routes are live behind Authelia:** `GET /approvals`, `GET /approvals/{order_id}`, `POST /approvals/{order_id}/decide`. The detail page works on a 375px-wide phone screen — confirmed in template; live mobile validation deferred to next signal.
- **Audit chain extended with two new kinds:** `hitl/pending_approval_added` (when an entry is registered) and `hitl/board_decision_received` (when a decision is resolved, tagged with source). Both are best-effort writes — registry continues working if audit DB is temporarily unavailable. The existing `board_approved` / `board_rejected` rows (written by graph nodes) remain unchanged.
- **TelegramChannel is now mode-aware about the registry.** When constructed with `registry=...`, message-send is registered as a notifier on `start()` and inline-keyboard / command resolution all flow through `registry.resolve(..., source="telegram")`. Without a registry, byte-identical legacy behavior (preserved for CLI dev + non-Telegram test paths).
- **Telegram inline keyboard still works in parallel.** Both surfaces (web + Telegram) converge at the same Future. The slim-format flag from Phase A stays OFF — soak window observes both surfaces with the rich body still shipping to Telegram.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`_run_order` is unchanged at the call site.** It still calls `await channel.request_approval(req)`. The redirection to `registry.wait` happens inside TelegramChannel — keeps the orchestrator API stable and means test_graph_hitl.py works without modification (it bypasses channels entirely via `Command(resume=...)`). Anyone who later wants the orchestrator to call `registry.wait` directly can do so; current shape is the smaller-diff option.
- **TelegramChannel keeps a vestigial `_pending` dict for legacy paths.** When constructed without a registry (CLI dev, older tests), the dict still owns the Future. Production always wires a registry → dict is unused. Don't delete the dict in a B.2 polish PR without auditing every TelegramChannel construction site.
- **`/approvals` reads from in-process state, not the audit DB.** A restart wipes the registry. Suspended LangGraph threads survive in the SqliteSaver checkpointer, but the registry itself doesn't auto-recover. Recovery (read recent `pending_approval_added` audit rows that don't have a matching `board_decision_received`, re-add to registry, re-emit notification) is a B-v2 polish item documented at `planning/hitl_in_app_design.md` §3 + §9. Today, a mid-approval restart loses the approval surface — the user re-triggers via the originating scout/webhook.
- **Modify intentionally NOT shipped in B.1.** Web POST returns 400 if `decision="modify"`. Telegram `/modify <id> <qty>` still works (resolves the registry directly). B.2 lands the web-side modify flow with htmx swap + form expansion.
- **`/approvals` route registered before the catch-all 404 handler.** FastAPI route order matters; verified by the 404-on-unknown-order-id test.
- **Phase A slim-format flag is unchanged.** `TELEGRAM_NOTIFICATION_ONLY` stays OFF on prod systemd. Soak Phase B.1 + Phase B.2 + Phase B.3 with rich body + web in parallel; flip the flag at B.4 after ~1 week of confidence.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 442 unit tests pass on local (vs 418 pre-B.1 baseline; +24 = 11 `test_pending_registry` + 13 `test_approvals_routes`). 5 pre-existing P2 failures unchanged (BACKLOG line 1247, PMCC scan liquidity gate — unrelated to B.1).
- md5 7/7 files MATCH between local and prod post-scp.
- Backup tag `.pre-b1-hitl-web-20260503-0349` placed on the 4 mutated prod files pre-deploy.
- PID 115197 → 118285 (restart at 03:50:38 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~36s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `PendingApprovalRegistry` present in `pending_registry.py` + imported in `telegram_bot.py` + `main.py`; `_notify_approval` + `_resolve_decision` present in `telegram_bot.py`; `pending_registry` field present on `WebDeps`; three new routes present in `routes.py`; new templates present in `web/templates/`.
- `GET /` 200 in 2.53s; `GET /research` 200 in 2.61s.
- `GET /approvals` 200 in 2.52s. Response body contains "No approvals pending" — empty state rendering correctly.
- `GET /approvals/nonexistent` 404 — detail-page guard works.
- journalctl post-restart: only errors are pre-existing Fidelity Azure-IP block (BACKLOG P1 #1276 — datacenter IPs flagged; same pattern as every restart since Fidelity scope was added) and yfinance BTC/USD earnings noise (external API hiccup, same pattern as prior 02:09 UTC + 00:05 UTC deploys). ZERO new errors related to registry / pending_approval / hitl / web routes / templates.

**Inert / dormant on current traffic:**
- **No real PMCC-scout-emitted approvals are pending right now** (Sun pre-market; next scheduled scan is Mon 2026-05-04 ~13:30 UTC). The `/approvals` page shows the empty state. First production exercise of the registry's full wait→resolve loop happens on Monday's first Board-routed scout output. Until then, the integration is exercised only by the test suite's mock orchestration.
- **Restart-recovery is NOT wired.** If trading-corp restarts mid-approval (e.g. a deploy lands during Board deliberation), the registry empties; the suspended LangGraph thread state survives in the SqliteSaver but the user has no surface to act on it without re-triggering. Acceptable for B.1; recovery is a B-v2 polish item.
- **`TELEGRAM_NOTIFICATION_ONLY` env stays unset.** Phase A's slim Telegram body remains dormant. Soak the parallel paths (rich Telegram + new web) for ~1 week before flipping at B.4.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-b1-hitl-web-20260503-0349; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/telegram_bot.py trading_corp/main.py trading_corp/web/app.py trading_corp/web/routes.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm -f \$BASE/trading_corp/comms/pending_registry.py \\
      \$BASE/trading_corp/web/templates/approvals.html \\
      \$BASE/trading_corp/web/templates/approval_detail.html;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 02:09 UTC — Phase A: HITL slim-Telegram bridge + PMCC prompt-text refinements (cooldown reframing + LEAP-Hard-Rule note + STD strike example)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction (BACKLOG P0 — "HITL approval flow lives in the web app; Telegram becomes notification-with-deeplink", NEW 2026-05-03). Phase A is the smallest cut of that P0: dormant `notification_only` switch in `TelegramChannel` + new slim-format builder + env-var wiring. Bundled with PMCC prompt-text clarifications (4 edits — COOLDOWN reframing in BS+STD blocks, BLACK_SHEEP LEAP-Hard-Rule NOTE, STANDARD STRIKE TARGETING regime-appropriate example) since the working-tree file was already mutated alongside the comms changes. User explicit go-ahead in-session 2026-05-03 ~02:00 UTC after deciding to skip the Monday PMCC scan validation gate (will validate live on next signal). The prior plan (BACKLOG ## 📦 PENDING DEPLOY) called for waiting until Mon ~13:30 UTC; that gate is dropped because the slim-format change is dormant by default and the prompt edits are LLM-facing only — first signal after deploy exercises both safely.
**Backup tag:** `.pre-phase-a-slim-telegram-20260503-0209` (on the 4 mutated files)

**Files deployed (4):**
- `trading_corp/comms/approval_format.py` — adds `format_slim_approval_notification(order, order_id, division, base_url)` returning a Markdown-Telegram body of the form `<ACTION> · <SYM> · <division>\n\n[Review on dashboard →](<base_url>/approvals/<order_id>)`. Existing `format_approval_message` (rich body) preserved unchanged. Defined at `approval_format.py:29`. URL is `{base_url}/approvals/{order_id}` — pair-coalescing happens server-side at the dashboard once Phase B ships, not in the formatter.
- `trading_corp/comms/telegram_bot.py` — `TelegramChannel.__init__` gains `notification_only: bool = False, dashboard_base_url: str | None = None` kwargs (line 40-41). `request_approval` branches on the flag (line 184): when False (default) emits the existing rich format; when True calls `format_slim_approval_notification`. Inline approve/reject keyboard preserved in BOTH modes during Phase A — lets us flip the flag the day Phase B ships without losing tap-to-approve until the web button is canonical.
- `trading_corp/main.py` — env-var wiring (line 500-507): `TELEGRAM_NOTIFICATION_ONLY=true` flips the flag; `DASHBOARD_BASE_URL` overrides the production default. Both default to safe values that preserve the rich body.
- `trading_corp/agents/divisions/pmcc_robinhood.py` — 4 prompt-text edits, no code-path changes:
  1. **`_BLACK_SHEEP_RULES` Rule 6 COOLDOWN reframed.** Was a NOTE explaining the deterministic `_recent_halfway_roll_cooldown` backstop. Now the rule itself instructs "HONOR the cooldown directly when ROLL HISTORY shows a recent halfway roll" with concrete acceleration-override math (`spot now > prior_short_strike_after + |prior_strike_change|`). Backstop language demoted to "BACKSTOP: the guard catches the override-vs-cooldown edge cases" — keeps the LLM and the guard semantically aligned rather than appearing to fight each other.
  2. **`_BLACK_SHEEP_RULES` LEAP-Hard-Rule NOTE added** (12 lines after the strict perpetual-roll philosophy section). Explains that `_promote_to_roll_leap_if_hard_rule` fires regardless of regime when LEAP delta>=0.95 OR DTE<120, AND that BS philosophy normally would defer LEAP exit longer — so when the guard promotes a roll_short → roll_leap on a BS position, the user can still reject the LEAP roll and approve only the short roll. Tells the LLM how to AVOID triggering the guard (choose `hold` or `watch` instead of `roll_short` until DTE crosses BS exit threshold).
  3. **`_STANDARD_RULES` BREACH POLICY COOLDOWN reframed** (parallel to BS Rule 6 above). Same "HONOR the cooldown directly" language + concrete acceleration math + BACKSTOP demotion.
  4. **`_STANDARD_RULES` STRIKE TARGETING example regime-appropriated.** Was "halfway midpoint = $X.XX" (a BS-shaped example). Now "roll above $200 resistance" — a STD-regime example. Also added explicit guidance to LEAVE `target_strike` null for normal cycle rolls where delta-target IS the selection criterion (avoids over-eager target_strike population that would defeat delta ranking).

**Local-only (NOT deployed):**
- `tests/test_slim_approval_notification.py` — 11 tests pinning the slim body shape (headline composition, division-omission when None, URL formatting, base_url trailing-slash handling, Markdown-link safety). Local-only: prod doesn't run tests in-tree.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Slim Telegram body shape is locked.** `format_slim_approval_notification` is the canonical function; any future caller that wants the slim ping (e.g. paired-roll coalesced URLs in Phase C) calls this. URL contract: `<base_url>/approvals/<order_id>` for individual orders. Pair-form (`/approvals/pair/<pmcc_pair_id>`) is reserved for Phase C and would need a sister function.
- **`TelegramChannel` is now mode-aware.** Instantiating with `notification_only=True` flips to slim; default False preserves rich body. Mode is per-instance, not per-call — so all approvals from a single channel use the same body shape.
- **Env contract:** `TELEGRAM_NOTIFICATION_ONLY` (default `false`) + `DASHBOARD_BASE_URL` (default the production URL) wired into the channel constructor in `main.py`. To activate slim mode in prod, set `TELEGRAM_NOTIFICATION_ONLY=true` on the systemd unit AFTER Phase B `/approvals/{id}` route exists. Until then: do not flip.
- **PMCC prompt rule clarifications LIVE on next analysis.** COOLDOWN clauses now cite the deterministic backstop explicitly; LEAP-Hard-Rule NOTE explains the cross-regime promotion path; STANDARD STRIKE TARGETING example is regime-appropriate. The Anthropic API call uses the new prompt text on every scan / re-analyze trigger from this restart forward.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **Slim mode is opt-in and dormant on this deploy.** Default `notification_only=False`; env vars unset on prod systemd unit (verified). Behavior at the Telegram-message layer is byte-identical to pre-deploy. The new code paths exist but are not exercised on production traffic.
- **Inline keyboard is preserved in slim mode (Phase A bridge behavior).** When `notification_only=True` activates (post-Phase-B), the message body shrinks to "headline + deeplink" but the inline keyboard stays so the existing Telegram approve/reject still works. Phase B's web button becomes the canonical surface; the keyboard goes away when the bridge is removed (likely Phase C or D — TBD in `planning/hitl_in_app_design.md`).
- **`format_slim_approval_notification` takes `order_id` explicitly,** not derived from `order` — because the order shape across callers (ProposedOrder vs DB-row dict) doesn't carry the id consistently. Caller is responsible for passing it.
- **PMCC prompt-text changes are LLM-facing only.** No new symbols introduced. The deterministic guards shipped at 00:05 UTC (`_recent_halfway_roll_cooldown`, `_promote_to_roll_leap_if_hard_rule`) and 00:36 UTC (`target_strike` plumbing) are unchanged. Symbol-presence on prod files re-verified post-deploy.
- **The COOLDOWN reframe shifts authority from "LLM informed by NOTE about backstop" to "LLM applies cooldown directly, backstop catches edge cases."** Net behavior should be: fewer cases where the LLM picks `roll_short` and the guard rewrites to `hold` (and the audit row reads inconsistently — LLM rationale says "roll", action says "hold"). Back-to-back halfway rolls are still prevented either way; the difference is the LLM's narration aligns with the action.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 418 unit tests pass on local (5 pre-existing P2 failures unchanged — same `_call`-helper liquidity-gate trap as prior deploys; not in this batch's blast radius).
- md5 4/4 files MATCH between local and prod post-scp.
- Backup tag `.pre-phase-a-slim-telegram-20260503-0209` placed on all 4 files pre-deploy.
- PID 113881 → 115197 (restart at 02:09:48 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~33s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `format_slim_approval_notification` (1 hit, definition + import in telegram_bot.py implied by 3 hits there); `notification_only` (3 hits — kwarg + assign + branch); `TELEGRAM_NOTIFICATION_ONLY` (2 hits — comment + getenv); `DASHBOARD_BASE_URL` (2 hits — comment + getenv).
- `GET /` 200 in 2.63s; `GET /research` 200 in 2.61s.
- journalctl post-restart: only errors are the pre-existing Fidelity Azure-IP block (BACKLOG P1 #1276 — datacenter IPs flagged at network layer; same pattern as every restart since Fidelity scope) and yfinance BTC/USD earnings noise (external API hiccup, same pattern as prior 00:05 UTC deploy line 160). No ImportError / NameError / AttributeError / Traceback related to the new code.
- Env sanity: `TELEGRAM_NOTIFICATION_ONLY` and `DASHBOARD_BASE_URL` neither set on systemd unit — slim mode dormant, as planned.

**Inert / dormant on current traffic:**
- **Slim Telegram format is dormant.** `notification_only=False` default + env unset → rich body is what gets sent. Activates only when (a) Phase B `/approvals/{id}` page exists on prod AND (b) `TELEGRAM_NOTIFICATION_ONLY=true` is added to the systemd unit. Until then this deploy is byte-for-byte equivalent at the Telegram-message layer.
- **PMCC prompt refinements active immediately on the LLM call path.** Anthropic API call uses the new prompt text on every scan / re-analyze trigger from this restart forward. First production exercise: next scheduled scan (Monday 2026-05-04 ~13:30 UTC) or any "Re-analyze" click before then. Per user's "validate live on next signal" decision, no synthetic test was run pre-deploy — the next real PMCC analysis is the validation event.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-phase-a-slim-telegram-20260503-0209; BASE=/home/azureuser/trading_corp;
for f in trading_corp/comms/approval_format.py trading_corp/comms/telegram_bot.py trading_corp/main.py trading_corp/agents/divisions/pmcc_robinhood.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 00:36 UTC — PMCC P1 (Item 3): target_strike honors LLM rule-driven strike (halfway-rule strike drift fix)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board pre-authored fix sketch in BACKLOG.md (P1 — PMCC roll: Recommended strike ignores the halfway-rule the expert text cites). User explicit go-ahead in-session 2026-05-02 after re-evaluating the residual risk under paper-mode + HITL: even if a wrong strike is recommended, it surfaces in the Telegram approval card and the Board catches it before placement. Closes the third of three related PMCC roll-correctness items shipped this session (sister DONE entries: roll-history blindness + LEAP-roll-missing).
**Backup tag:** `.pre-pmcc-target-strike-20260503-0035` (on the 1 mutated file)

**Files deployed (1):**
- `trading_corp/agents/divisions/pmcc_robinhood.py` — five touchpoints, all threading the new `target_strike` field through the existing call chain:
  1. **`PMCCAnalysis` dataclass** gains `target_strike: float | None = None`. Backwards-compat default — existing callers that don't supply it get None and behave identically. Field annotated with the role: when set, strike picker honors it and overrides delta-distance ranking.
  2. **`_select_weekly_strike(calls, target_delta, target_strike=None)`** — when `target_strike` is set, picks the listed strike whose `strike_price` is closest to it (ignoring delta entirely). Caller is responsible for sanity — we don't second-guess (the LLM cited the strike per its rules; e.g. an ITM defensive halfway-roll). When `target_strike` is None, falls through to the original delta-distance behavior with OTM-only filtering. Defensive: returns None if no eligible strike (no strike_price field).
  3. **`_find_best_weekly(symbol, broker, target_delta=None, target_dte=None, target_strike=None)`** — accepts target_strike, threads to `_select_weekly_strike`. DTE/expiry-window selection unchanged.
  4. **LLM prompt JSON schema** (in `_llm_analyze_position`) — added `"target_strike": <recommended short call STRIKE as float, or null>` to the response template. Field annotated as: "set this when a rule prescribes a specific strike (e.g. halfway-roll midpoint per BREACH HANDLING). When set, the strike picker honors this directly, overriding delta-distance ranking. Leave null when delta-targeting is correct (standard cycles)."
  5. **JSON parse** (in `_llm_analyze_position`) — extracts `target_strike` with the same float-or-None pattern as `target_delta`. Defensive: if the LLM omits the field, falls through to None (no strike override, original behavior).
  - **Threaded through 5 callers** of `_find_best_weekly` (single-line in 4 places, multi-line in 1): `propose_orders_for_pair` `roll_leap` 4th-leg, scan-path inline `roll_leap` 4th-leg, `_propose_open_pmcc`, `_propose_sell_weekly`, `_propose_roll_short`. Each adds `target_strike=analysis.target_strike if analysis else None`.
  - **Prompt rule corpus updated:** `_BLACK_SHEEP_RULES` Rule 6 (BREACH HANDLING) and `_STANDARD_RULES` BREACH POLICY each gained a STRIKE TARGETING clause instructing the LLM to populate `target_strike` when narrating a specific strike (halfway midpoint or rule-cited target). Without this clause the LLM had no signal that the new field existed; with it, the rule application is coherent end-to-end.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`PMCCAnalysis.target_strike` is now a real field.** Anywhere downstream that needs to know what strike the LLM cited can read `analysis.target_strike` directly instead of regexing the rationale text.
- **Strike picker honors LLM-cited strikes.** When the LLM applies a rule like "Major Breach → halfway midpoint = $X.XX" and populates `target_strike`, the recommendation card's open leg lands at the listed strike closest to that value. Pre-fix the picker fell back to `target_delta` ranking, which on high-IV underlyings typically picked a strike well above the cited halfway midpoint — the BACKLOG-cited MSTR symptom (cited $169, picked $187.50). BACKLOG.md "P1 — PMCC roll: Recommended strike ignores the halfway-rule the expert text cites" → DONE.
- **Backwards-compat preserved.** `target_strike=None` (the default + the LLM's response when omitted) → original delta-distance behavior. No drift on standard-cycle recommendations. Pinned by `test_propose_roll_short_falls_back_to_delta_when_target_strike_none`.
- **All 5 `_find_best_weekly` call sites are wired.** Both `roll_leap` 4-leg branches (propose_orders_for_pair + scan-path) honor target_strike on the new-short leg, so a halfway-into-LEAP-roll scenario gets both LEAP rolled AND new short at the LLM-cited strike.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`target_strike` overrides target_delta when both are set.** This is intentional — the LLM populates target_strike only when it has a rule-driven specific target; in that case the delta is incidental. If you ever need both honored simultaneously, change `_select_weekly_strike` to filter by delta range and minimize strike distance within that pool. Today's behavior is "strike wins" because that's what the rule citation requires.
- **No spot-acceleration check in the picker.** Same rationale as the cooldown guard from the prior deploy: keep the deterministic helper simple; the LLM's rule corpus is responsible for choosing the right strike based on regime/IV context. The picker just honors the choice.
- **Prompt rule clauses added to BOTH `_BLACK_SHEEP_RULES` Rule 6 AND `_STANDARD_RULES` BREACH POLICY.** Both regimes need the STRIKE TARGETING note because both regimes can cite specific strikes (halfway-roll for black sheep on Major/Runaway breach; up-and-out for standard on Major). If a future regime-specific rule block is added (e.g. crypto-options-specific), it needs the same clause.
- **The 5 caller threading is mechanical.** If a future `_find_best_weekly` caller is added (e.g. a new strategy variant), pattern-copy the existing `target_strike=analysis.target_strike if analysis else None` line.

**Latent bugs caught + fixed:** none. The 5 pre-existing P2 PMCC scan failures (BACKLOG.md line 1093) remain unchanged; my new tests use the local `_liquid_call` helper introduced in the prior deploy to avoid the same trap.

**Verification:**
- Pre-deploy: 407 unit tests pass on local (vs 397 baseline before this deploy; +10 = 4 strike-picker tests, 2 dataclass tests, 1 `_find_best_weekly` integration, 2 `_propose_roll_short` end-to-end, 1 defensive). 5 pre-existing P2 failures unchanged.
- md5 1/1 file MATCH between local and prod post-scp.
- Backup tag `.pre-pmcc-target-strike-20260503-0035` placed on `pmcc_robinhood.py` pre-deploy.
- PID 112932 → 113881 (restart at 00:35:50 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~45s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol presence: `target_strike` appears 26 times in prod's `pmcc_robinhood.py` (definition + dataclass + 5 callers + parse + prompt schema + 2 rule clauses + tests in comments + helper signature). Local matches.
- `GET /` 200 in 2.75s; `GET /research` 200 in 2.62s.
- journalctl post-restart: zero errors of any kind in the filtered window. Service loaded cleanly.

**Inert / dormant on current traffic:**
- The `target_strike` override is dormant until the LLM populates the field on its next analysis. The Anthropic API call is live on the existing scan schedule; the prompt rule clause is the trigger that gets the LLM to populate it. Expect the next scheduled scan (Monday 2026-05-04 ~13:30 UTC) to be the first time the field gets exercised live for a Board-visible recommendation.
- Backwards-compat path (target_strike=None) is what every current cached analysis (if any) and every LLM response that doesn't include the new field will exercise. This path is byte-identical to pre-deploy behavior.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-target-strike-20260503-0035; BASE=/home/azureuser/trading_corp;
mv \$BASE/trading_corp/agents/divisions/pmcc_robinhood.py\$TAG \$BASE/trading_corp/agents/divisions/pmcc_robinhood.py;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 00:05 UTC — PMCC P1 guards: LEAP Hard Rule promotion + halfway-roll cooldown + roll_leap 4-leg compound

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board pre-authored fix sketches in BACKLOG.md (P1 — PMCC roll: LLM analyzer is blind to recent roll history; P1 — PMCC drilldown: Recommended Trade omits the LEAP roll when both legs need to roll). User explicit go-ahead in-session 2026-05-02. Both items are real-money correctness gaps: (a) the LLM analyzer was recommending back-to-back halfway rolls because its prompt had zero history; (b) when LEAP delta>=0.95 OR DTE<120 the recommendation card was emitting a 2-leg roll_short instead of a 4-leg roll_leap, leaving a fresh short on a dying LEAP if approved.
**Backup tag:** `.pre-pmcc-p1-guards-20260502-2357` (on the 2 mutated files)

**Files deployed (2):**
- `trading_corp/agents/divisions/pmcc_robinhood.py` — five additions, three deterministic-then-narrate guards plus the 4-leg `roll_leap` extension:
  1. **`_promote_to_roll_leap_if_hard_rule(analysis, leg)`** (Item 2). Promotes `roll_short` / `roll_short_early` → `roll_leap` when `leg.long_leg_delta >= 0.95` OR `leg.long_leg_dte < 120`. Honors CLAUDE.md §1's deterministic-then-narrate principle — the LEAP Hard Rule trigger is purely a function of already-computed numeric state, so it shouldn't ride through LLM judgment. Adds an explanatory warning to `analysis.warnings` so the audit trail + Telegram approval message render the reason.
  2. **`_recent_halfway_roll_cooldown(analysis, leg)`** (Item 1). Backstop guard that downgrades `roll_short` → `hold` when a recent roll-up (positive strike_change >= $1) was executed within `cooldown_days` (default 7) AND short DTE > terminal_dte_floor (default 2) AND extrinsic > extrinsic_floor (default $0.50/sh). The LLM also gets the rule clause + ROLL HISTORY block in its prompt and should already prefer HOLD; this guard is the deterministic backstop.
  3. **`_query_prior_rolls_detailed(symbol, leap_lifetime_key)`** (Item 1). Sister to the existing `_query_prior_rolls` — same SQL/grouping but returns `last_roll_ts`, `last_roll_short_strike_before`, `last_roll_short_strike_after`, `last_roll_strike_change`, `days_since_last_roll`. Used by the cooldown guard AND the prompt formatter. `leap_lifetime_key` scoping mirrors the existing helper (pre-fix NULL-keyed pairs preserved; mismatched-key pairs filtered).
  4. **`_format_roll_history_block(leg)`** (Item 1). Builds the ROLL HISTORY section injected into `_llm_analyze_position`'s prompt. Empty string when no DB; "No prior rolls" copy when DB is empty for this LEAP; otherwise count + net dollars + most-recent strike change with a roll-up/roll-down label.
  5. **4-leg `roll_leap` compound.** Both `roll_leap` branches (`propose_orders_for_pair` line ~1085 and the inline scan-path branch line ~1921) extended to emit a 4th order: open new short on the new LEAP. Skipped gracefully if no qualifying weekly chain — next scan picks up the uncovered LEAP via the `open_short` branch. Pre-fix the recommendation was 3 legs (close short + close LEAP + open new LEAP), which would leave the user uncovered if approved as-is. The BACKLOG entry's verification text required the 4-leg compound; the entry's claim that "the existing roll_leap action DOES already build a compound roll (close short + close LEAP + open new LEAP + open new short)" was inaccurate — this deploy makes that claim true.
  - Composition order at both call sites: `_terminal_dte_time_release` → `_promote_to_roll_leap_if_hard_rule` → `_recent_halfway_roll_cooldown`. Rationale: terminal-DTE first because deadline-driven rolls need to ship; Hard-Rule second because if the LEAP is dying, that lifts roll_short to roll_leap (cooldown is a no-op on roll_leap so a needed LEAP roll isn't silently vetoed); cooldown last as the pure backstop. Test `test_cooldown_does_not_fire_after_hard_rule_promotion` pins this composition.
  - Prompt updates: `_BLACK_SHEEP_RULES` Rule 6 (BREACH HANDLING) gained a COOLDOWN clause; `_STANDARD_RULES` BREACH POLICY gained the parallel clause; `_STANDARD_RULES` Rule 5 (HARD RULES) gained a NOTE about the LEAP-Hard-Rule promotion. ROLL HISTORY block injected before the JSON-response request in `_llm_analyze_position`.
- `config/strategies.yaml` — new `robinhood_pmcc.roll_cooldown` block: `cooldown_days: 7`, `extrinsic_floor: 0.50`, `min_strike_change: 1.0`, `terminal_dte_floor: 2`. Hot-reloadable via the same mtime-cache mechanism the rest of `_cfg` uses.

**Features shipped (load-bearing for future "is X done?" checks):**
- **LEAP Hard Rule promotion is live.** When a PMCC scan or dashboard "Approve & Execute" produces an analysis with `roll_short` action and the LEAP has delta >= 0.95 OR DTE < 120, the action is silently promoted to `roll_leap` and the recommendation card now shows all four legs. Distracted-approval risk on dying-LEAP scenarios eliminated for these conditions. BACKLOG.md "P1 — PMCC drilldown: Recommended Trade omits the LEAP roll when both legs need to roll" → DONE.
- **Halfway-roll cooldown is live.** When the prior fill on a LEAP's lifetime was a roll-up (>= $1 strike change) within the last 7 days AND the current short isn't deadline-driven AND extrinsic is non-trivial, `roll_short` is downgraded to `hold` with a warning. Back-to-back halfway-roll waste avoided. BACKLOG.md "P1 — PMCC roll: LLM analyzer is blind to recent roll history (recommends back-to-back halfway rolls)" → DONE.
- **`roll_leap` action emits a 4-leg compound** (close short + close LEAP + open new LEAP + open new short) instead of the prior 3-leg shape. Applies to BOTH dispatch sites — `propose_orders_for_pair` (used by dashboard "Approve & Execute" + Telegram per-pair approval) and the scheduled-scan inline branch.
- **`_query_prior_rolls_detailed` is now available** as a sister to `_query_prior_rolls`. Future callers needing per-roll metadata (strike change, last-roll ts, days-since) use this; old callers (Phase 2 position-context) keep using the simpler tuple shape unchanged.
- **LLM prompt now includes a ROLL HISTORY block** scoped to the current LEAP's `leap_lifetime_key`. The narration the LLM produces when ROLL HISTORY shows a recent roll-up should now coherently cite the cooldown rule even before the deterministic guard fires.
- **`config/strategies.yaml` carries `roll_cooldown` knobs.** Tuning the cooldown window or thresholds is a one-line YAML edit + service restart.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **The 4-leg `roll_leap` is a real-money path expansion.** Pre-fix the action emitted 3 legs; post-fix it emits 4 (or 3 with a logged "no qualifying weekly" note when the new-short fallback can't fill). Anything that previously assumed `roll_leap` produced exactly 3 legs (tests, audit-grep tooling, Telegram message templates) needs to handle the 4th leg gracefully. Quick scan: nothing in the repo greps on `roll_leap_close_short`/`roll_leap_close`/`roll_leap_open` count assumptions; new audit kind `roll_leap_open_short` mirrors the existing pattern (action stashed in `extra["action"]`).
- **Composition order at both call sites is load-bearing.** `_terminal_dte_time_release` → `_promote_to_roll_leap_if_hard_rule` → `_recent_halfway_roll_cooldown`. Re-ordering would allow the cooldown to veto a needed LEAP roll (if cooldown ran before Hard-Rule promotion). Test `test_cooldown_does_not_fire_after_hard_rule_promotion` pins it.
- **The cooldown's "is this a halfway-style roll-up" detector is a heuristic.** It uses `last_roll_strike_change >= min_strike_change` (default $1.00) — captures halfway-roll-into-breach and OTM target-delta roll-ups; excludes near-zero same-strike cycle drift. Doesn't try to detect the spot-acceleration override in Python (that belongs in the LLM rule clause where regime/IV context is available). False-positive cooldown costs "user overrides via Telegram"; false-negative costs "back-to-back halfway-roll waste." Bias is intentionally toward HOLD.
- **The cooldown queries by `leap_lifetime_key`** — multi-LEAP-on-one-symbol scenarios won't cross-contaminate. Pre-fix history (NULL keys) still folds into the count, same backwards-compat as `_query_prior_rolls`.
- **The ROLL HISTORY prompt block is empty for fresh positions.** No DB query at all when `_db_url` is unset (test/CLI path). When DB present but no prior rolls, prompt gets "No prior rolls recorded for this LEAP." — the LLM sees the absence explicitly rather than missing the section entirely.
- **Both new methods live near `_terminal_dte_time_release`** in the file, reflecting the pattern they share (deterministic post-processor on PMCCAnalysis that returns a possibly-modified `dataclasses.replace`).

**Latent bugs caught + fixed:** none specific. The 5 pre-existing P2 PMCC scan failures (BACKLOG.md line 1093, "5 PMCC scan tests failing on liquidity gate") are unchanged — same failures, same root cause (test fixture's `_call` helper omits `open_interest` + `volume`, fails the standard liquidity gate). My new tests use a local `_liquid_call` helper to avoid the same trap.

**Verification:**
- Pre-deploy: 397 unit tests pass on local (vs 370 baseline before this session; +27 = `_promote_to_roll_leap_if_hard_rule` × 7, `_recent_halfway_roll_cooldown` × 11 including composition, `_query_prior_rolls_detailed` × 4, `_format_roll_history_block` × 3, `roll_leap` 4-leg integration × 2). 5 pre-existing P2 failures unchanged.
- md5 2/2 files MATCH between local and prod post-scp.
- Backup tag `.pre-pmcc-p1-guards-20260502-2357` placed on both files pre-deploy (verified file sizes).
- PID 111560 → 112932 (restart at 00:05:31 UTC). ActiveState=active SubState=running.
- Port 8000 came up ~45s after restart (Robinhood + Fidelity logins block bind, normal).
- Symbol-presence checks on prod files: `_promote_to_roll_leap_if_hard_rule` (4 hits), `_recent_halfway_roll_cooldown` (7 hits — counts include doc references in rule blocks + call sites + definition), `_query_prior_rolls_detailed` (4 hits), `roll_leap_open_short` (2 hits — both branch implementations), `roll_cooldown:` (1 hit in strategies.yaml).
- `GET /` 200 in 2.91s; `GET /research` 200 in 2.78s; `GET /partials/trade-flow` 200.
- journalctl post-restart: only error is a transient `yfinance HTTP 500` (external API hiccup, unrelated to deploy). No ImportError / NameError / AttributeError / Traceback related to the new code.

**Inert / dormant on current traffic:**
- The cooldown guard requires a recent FILLED roll on the same symbol within the cooldown window. Today the bot is paper-mode (`auto_execute: false` everywhere) so there are no filled real-money rolls — the guard will never fire on production traffic until either (a) auto_execute flips for PMCC, or (b) the Board approves a real roll via Telegram and the data_exec path writes a `filled` row. Until then, the guard is wired but inactive. Test coverage exercises it via seeded `proposed_order` rows.
- The Hard-Rule promotion fires whenever the LLM analyzer emits `roll_short` on a position with delta>=0.95 OR DTE<120. Several current Robinhood positions are within those LEAP conditions (per the dashboard); the next scheduled scan or "Re-analyze" click will exercise the promotion live.
- The 4th `roll_leap` leg fires the moment any `roll_leap` action is approved. Until a Board approval lands one, the new code path is dormant — but observable in the recommendation card preview as soon as the next scan produces a `roll_leap` recommendation.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-p1-guards-20260502-2357; BASE=/home/azureuser/trading_corp;
for f in trading_corp/agents/divisions/pmcc_robinhood.py config/strategies.yaml; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 23:03 UTC — PMCC research-as-consultant validation surface (05-05 review tooling)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board direction in-session — the 2026-05-02 vision realignment created a 3-day observation period (2026-05-02 → 2026-05-05) for PMCC's `universe_source: research_on_demand` integration. Decision criteria per the realignment memo: (a) count of `research_candidate_recommendation_emitted` rows from PMCC scout, (b) count of those that produced downstream order activity, (c) qualitative read on whether the research-recommended candidates are ones PMCC would have surfaced on its own. Without dedicated tooling that decision is a vibes-call on ad-hoc SQL on 05-05; this surface makes it tractable.
**Backup tag:** `.pre-pmcc-validation-view-20260502-2301` (on the 3 mutated files)

**Files deployed (3):**
- `trading_corp/agents/logger.py` — new `LoggerAgent.events_since(ts_iso, limit=5000)` method. Date-scoped audit fetch for multi-day windows that would overflow `recent_events()`'s limit. Returns newest-first. Used by the new validation view; existing `recent_events()` callers unaffected.
- `trading_corp/web/routes.py` — new `_build_pmcc_validation_view(deps)` joins three sources: `research_candidate_recommendation_emitted` (engagement-level + candidate list) ⨝ `research_candidate_acted_on` / `research_candidate_skipped` (per-candidate division row, keyed by `(engagement_id, symbol)`) ⨝ `proposed_order.status` (downstream lifecycle for acted_on candidates' order ids). Computes scoreboard counts + skip-reason histogram. Hard-coded observation window start = `2026-05-02T00:00:00Z`. Wired into `_build_research_view` return as `view.pmcc_validation`. New helper `_lookup_order_statuses(deps, order_ids)` does a bulk SELECT against `proposed_order` for the acted_on rows' order ids. New `_empty_pmcc_validation_view()` so the empty-deps branch returns the right keys.
- `trading_corp/web/templates/research.html` — new section "PMCC research-as-consultant validation" inserted between Engagement-latency and Recommendation-outcomes sections. Top scoreboard (Engagements / Candidates / Acted on / Skipped / Approved/filled — 5 numbers in a 5-col grid). Skip-reason histogram strip below. Per-engagement collapsible cards (newest open by default) with a 6-column candidate table: Symbol / Conviction / Fit / Status pill / Order status / thesis-or-skip-reason. Status pills color-code: acted=gain, skipped=warn, no-outcome=muted. Order-status colors: filled=gain, cancelled/risk_rejected/board_rejected=loss, others=muted.

**Features shipped (load-bearing for future "is X done?" checks):**
- **`/research` now has a "PMCC research-as-consultant validation" section** showing the per-engagement candidate-level breakdown that the 05-05 review needs. Today renders empty-state ("No PMCC research engagements yet in the observation window") because no PMCC scout cycle with `universe_source: research_on_demand` has fired and completed an engagement yet — the surface is in place for when one does. Confirms the 05-05 review will not require ad-hoc SQL.
- **`LoggerAgent.events_since(ts_iso, limit=5000)`** is now available for any future caller that needs a date-scoped audit fetch (vs `recent_events`'s row-count cap). Internal usage only today; no external API surface.
- **`PMCC_OBSERVATION_PERIOD_START` constant** in `trading_corp/web/routes.py` pins the observation window. After 2026-05-05 the constant can stay (surface remains useful as a longitudinal view) or be moved to a config knob if the period needs to slide.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **PMCC's HITL flow does NOT write `would_have_placed`** — only Otter/Cypher webhook handlers do. The realignment memo phrased the 05-05 criterion as "candidates that produced `would_have_placed` rows" but PMCC's LangGraph flow goes proposed → risk_approved → board_approved → filled (no `would_have_placed` step in between). This view surfaces the actual `proposed_order.status` lifecycle for acted_on candidates instead. Documented in `_build_pmcc_validation_view` docstring. Don't re-litigate; the realignment memo's wording was imprecise on this point and the truth is in the code.
- **The view is purely additive on `/research`.** The pre-existing `Recommendation outcomes` section (per-engagement act/skip counts) is intentionally kept — it's broader (cross-division) while the new section is PMCC-specific candidate-level depth. They aren't redundant; they answer different questions.
- **Join key for division-side rows is `(engagement_id, symbol.upper())`.** If a future audit-write path stops uppercasing the symbol on either side, the join breaks silently. Test `test_full_join_acted_on_skipped_no_outcome` pins the casing.
- **Order status lookup is best-effort.** If the proposed_order id is None on an acted_on row (defensive — shouldn't happen in practice), order_status renders as None and n_board_approved_or_filled doesn't increment. Test `test_acted_on_without_order_id_surfaces_none_status` pins this.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 370 unit tests pass on local (vs the 360-baseline in prior deploy logs — the +10 are the new `tests/test_pmcc_research_validation_view.py`). 5 pre-existing P2 PMCC-scan failures unchanged.
- md5 3/3 files MATCH between local and prod post-scp.
- PID 109394 → 111560 (restart at 23:02:35 UTC). Service active.
- Port 8000 came up ~10s after restart (Robinhood + Fidelity logins block bind).
- `GET /research` returns 200 in 2.60s. New section renders. Markers present in HTML: `PMCC research-as-consultant validation` (1), `observation since` (1), `decision date 2026-05-05` (1). All 5 scoreboard labels visible (Engagements, Candidates, Acted on, Skipped, Approved/filled). Empty-state copy `No PMCC research engagements yet in the observation window` (1) — expected, no engagements yet.
- `GET /` 200 in 2.58s; `GET /partials/trade-flow` 200.
- Zero new errors in journalctl post-restart aside from the pre-existing yfinance BTC earnings noise + Fidelity bot-block (both called out in earlier deploy logs).

**Inert / dormant on current traffic:**
- The validation section is empty-state today because no PMCC `universe_source: research_on_demand` cycle has produced an engagement yet. It will populate as engagements complete. If on 05-05 it's still empty, that's the answer to the validation question (research isn't being exercised).

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-pmcc-validation-view-20260502-2301; BASE=/home/azureuser/trading_corp;
for f in trading_corp/agents/logger.py trading_corp/web/routes.py trading_corp/web/templates/research.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 22:10 UTC — Live trade flow: tile open-state persists across htmx 5s refresh

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board reported the expand-on-click tiles auto-closing
in the browser. Initially attributed to cursor movement; verified via
the 6-second stillness test (open a tile, don't move mouse, count to 6
— tile closes on its own). Confirmed: htmx's `every 5s` outerHTML
refresh of `#trade-flow` rebuilds every `<details>` element fresh,
which discards the `open` attribute. The original Phase A spec
("BACKLOG.md 2026-05-02 — Live trade flow: expand-on-click tiles")
explicitly said "Open tiles will collapse on refresh unless the JS
preserves state. Decision: accept the collapse-on-refresh; if it gets
annoying, add a localStorage open-set keyed by audit_event row id
later." Got annoying within hours of shipping, so building it now.
**Backup tag:** `.pre-tradeflow-state-20260502-2208` (on 3 mutated files;
`trade_flow_state.js` is first-shipment, no backup needed)

**Files deployed (4):**
- `trading_corp/web/data.py` — `trade_flow()` SELECT now includes the
  `audit_event.id` column; the returned dict carries `"id": r["id"]`
  alongside the existing `ts/kind/symbol/side/qty/reason/payload_pretty`
  keys. `id` is the stable primary-key from `audit_event` and is the
  natural choice for keying tile-open state across htmx swaps.
- `trading_corp/web/templates/partials/trade_flow.html` — each tile's
  `<details>` element now carries `data-tile-id="{{ evt.id }}"`. JS
  uses this attribute to recognize the same tile across the htmx swap.
- `trading_corp/web/static/js/trade_flow_state.js` — NEW, ~75 lines of
  vanilla ES6, no new dependencies. Three responsibilities:
    1. Listen for `<details>` toggle events (capture phase, since
       `toggle` doesn't bubble) on `#trade-flow details[data-tile-id]`
       and persist the open-set to `localStorage` under key
       `tradeflow:open-tile-ids`.
    2. Listen for `htmx:afterSwap` events targeting `#trade-flow` and
       re-apply `open` attribute to any `<details data-tile-id>`
       whose ID is in the persisted set.
    3. Apply the same logic on initial `DOMContentLoaded`.
  No bounded-size cleanup of the persisted set: audit_event ids grow
  monotonically; the JSON encoding is small; localStorage caps in the
  5-10MB range per origin. Many years of normal trading before this
  becomes a real concern.
- `trading_corp/web/templates/home.html` — added
  `<script src="/static/js/trade_flow_state.js"></script>` to
  `{% block scripts %}` alongside the existing `equity_chart.js`.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Live trade flow tile open state survives the htmx 5s refresh.**
  Click a tile, walk away, come back later — it's still expanded.
- **State also survives page reload** (localStorage, not just JS memory).

**Notable code changes (callouts a future Claude shouldn't miss):**
- Open tile IDs persist across browser sessions in localStorage —
  someone debugging "why is this tile open before I touched it?"
  should check `localStorage.getItem('tradeflow:open-tile-ids')` in
  the browser console.
- This pattern (data-id attribute + capture-phase toggle listener +
  htmx:afterSwap re-apply) is reusable. If a future panel adds the
  same htmx-refresh-collapses-state issue (e.g. an Engagements log
  on the Research screen if it ever gets htmx polling), copy the
  trade_flow_state.js shape and parameterize on panel ID + selector.
- The Engagements log on `/research` ALSO uses the `<details>` expand
  pattern but does NOT have htmx polling, so it's unaffected by this
  bug and needs no preservation logic. If a future change adds htmx
  polling to that panel, it will need this same treatment.

**Latent bugs caught + fixed:** none.

**Verification:**
- PID 108238 → 109409 confirms restart at 22:10:30 UTC.
- All endpoints 200: `/` (2.9s), `/partials/trade-flow`, `/static/js/trade_flow_state.js`.
- Content checks: `data-tile-id` count in `/partials/trade-flow`
  render = 12 (one per tile). `trade_flow_state.js` referenced once
  in `/`.
- Browser test: Board (Jack) opened a Live trade flow tile, waited
  through one htmx tick without cursor movement, confirmed tile
  stayed open. Click again to close worked. Reload-and-restore
  worked.
- Zero errors in journalctl post-restart aside from the baseline
  paper_trade_replay info-level lines containing the literal string
  `'errors': 0`.

**Inert / dormant on current traffic:** none.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-tradeflow-state-20260502-2208; BASE=/home/azureuser/trading_corp;
for f in trading_corp/web/data.py trading_corp/web/templates/partials/trade_flow.html trading_corp/web/templates/home.html; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
rm \$BASE/trading_corp/web/static/js/trade_flow_state.js;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 21:52 UTC — Strategy file move: divisions/ → strategies/ (vocabulary realignment)

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board vision realignment in-session (2026-05-02). User
stepped back and articulated the model: divisions = brokerage/account
portfolio managers; strategies = how a division operates. Lord Otter and
Market Cypher had been mis-classified as divisions in `agents/divisions/`;
they are strategies running inside the `coinbase_spot` division. File
move + import updates align code with the corrected vocabulary. Pure
rename — zero behavioral change. Local md5 of moved files matched prod's
old `divisions/*.py` md5 byte-for-byte before deploy.
**Backup tag:** `.pre-strategy-rename-20260502-2146` (on 5 mutated files;
the 3 new `strategies/*.py` files are first-shipment, no backup needed)

**Files deployed (8):**
- `trading_corp/agents/strategies/__init__.py` — NEW (empty namespace package)
- `trading_corp/agents/strategies/lord_otter.py` — NEW; identical content
  to the just-deleted `divisions/lord_otter.py` (md5 `3011ed78…`)
- `trading_corp/agents/strategies/market_cypher.py` — NEW; identical
  content to the just-deleted `divisions/market_cypher.py` (md5 `b7e387b6…`)
- `trading_corp/main.py` — both wiring imports flipped from
  `agents.divisions.{lord_otter,market_cypher}` to
  `agents.strategies.{...}`
- `BACKLOG.md` — new top-of-file `## ⏸ PAUSED — Lord Otter + Market
  Cypher feature work` section documenting the maintenance-mode posture
  + 2026-05-02→2026-05-05 PMCC research-as-consultant observation
  period; two prose path references updated
- `config/strategies.yaml` — single comment-line path reference updated
  (cosmetic — values unchanged)
- DELETED: `trading_corp/agents/divisions/lord_otter.py`
- DELETED: `trading_corp/agents/divisions/market_cypher.py`

**Features shipped (load-bearing for future "is X done?" checks):**
- **Strategy modules now live at `trading_corp/agents/strategies/`**, not
  `trading_corp/agents/divisions/`. Any future Claude session searching
  for Otter/Cypher code by path should look at `strategies/`.
- **Logger namespace flipped:** all log lines from these agents now
  prefix with `trading_corp.agents.strategies.lord_otter` /
  `…market_cypher` instead of the old `…divisions.…`. Any external
  log-grep, journalctl filter, or audit query keyed on the old
  namespace will miss new entries.
- **BACKLOG.md ⏸ PAUSED notice is live on prod** — future sessions can
  see the maintenance-mode posture without needing chat context.
- **CLAUDE.md does NOT ship to prod** (it's a Claude-Code-only artifact
  per md5-diff finding); the new "§ Research consultation" rule + the
  module-map split + the divisions-table reframe live on the local
  working tree only. That's correct — CLAUDE.md is loaded per-session
  from local, not from prod's filesystem.

**Notable code changes (callouts a future Claude shouldn't miss):**
- `agents/divisions/` on prod NOW correctly holds only the actual
  divisions: `pmcc_robinhood.py`, `fidelity_options.py`,
  `crypto_futures/`. Plus a pile of `.pre-*` backup tags.
- `pmcc_robinhood.py` and `fidelity_options.py` STILL conflate
  division-level and strategy-level concerns — flagged in CLAUDE.md
  § Known sharp edges as future cleanup once a second strategy on
  Robinhood or Fidelity is needed. Don't refactor speculatively.
- `docs/ARCHITECTURE.md § 1 principle 2` quotes the OLD framing
  ("broker × strategy combo is its own division"). Officially
  superseded by CLAUDE.md's new framing as of 2026-05-02 — separate
  Board-approved ARCHITECTURE.md pass needed to update the source doc.

**Latent bugs caught + fixed:** none.

**Verification:**
- Pre-deploy: 55 unit tests pass on local against the renamed paths
  (`tests/test_lord_otter_bias_persistence.py`, `…webhook_audit_trail.py`,
  `…webhooks_return_fast.py`, etc.) — confirmed import path works.
- Post-deploy: PID 107062 → 108223 (restart at 21:52:26 UTC).
- `journalctl -u trading-corp --since '3 min ago'` shows
  `INFO trading_corp.agents.strategies.lord_otter: LordOtterAgent
  reloaded config: enabled=True auto_execute=False symbols=['BTC/USD']
  arming_window_bars=5` and the parallel Cypher line at the new
  namespace — confirms imports succeeded and agents loaded.
- Zero `ImportError` / `ModuleNotFoundError` / `agents.divisions.lord_otter`
  / `agents.divisions.market_cypher` lines in journalctl post-restart.
- Dashboard `/` returns 200 in 2.7s; `/research` 200; `/partials/trade-flow`
  200 in 3ms.

**Inert / dormant on current traffic:**
- The Otter+Cypher strategy modules run as before (paper-mode,
  `auto_execute: false`); no new feature work landed in this deploy.
  The pause is an organizational stance, not a code-level mute.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-strategy-rename-20260502-2146; BASE=/home/azureuser/trading_corp;
mv \$BASE/trading_corp/main.py\$TAG \$BASE/trading_corp/main.py;
mv \$BASE/BACKLOG.md\$TAG \$BASE/BACKLOG.md;
mv \$BASE/config/strategies.yaml\$TAG \$BASE/config/strategies.yaml;
mv \$BASE/trading_corp/agents/divisions/lord_otter.py\$TAG \$BASE/trading_corp/agents/divisions/lord_otter.py;
mv \$BASE/trading_corp/agents/divisions/market_cypher.py\$TAG \$BASE/trading_corp/agents/divisions/market_cypher.py;
rm -rf \$BASE/trading_corp/agents/strategies/;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 21:44 UTC — Dashboard polish: expand-on-click for Engagements log + Live trade flow + Engagement-latency column rename

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Board picked one of the new P5 UI polish items as a
warmup ("Engagements log expand-on-click" → routes.py + research.html);
extended to the symmetric trade-flow tile expand (data.py +
partials/trade_flow.html); the previously-PARTIALLY-DONE
"Engagement-latency panel column rename" was sitting on local working
tree and rode along.
**Backup tag:** `.pre-dashboard-polish-20260502-2143` (on all 4 files)

**Files deployed (4):**
- `trading_corp/web/routes.py` — added `import json`; new
  `"payload_pretty": json.dumps(payload, indent=2, default=str,
  sort_keys=True)` key on the `engagement_log` dict in
  `_build_research_view` (line 1044). `sort_keys=True` so repeated
  `kind`s render with stable field order across reloads.
- `trading_corp/web/data.py` — same `payload_pretty` key added to
  `trade_flow()` dict (line 932). `json` already imported.
- `trading_corp/web/templates/research.html` — engagement_log row
  `<div>` converted to `<details class="px-4 py-2 group">` /
  `<summary>` matching the existing thesis-library pattern. Body is
  a `<pre>` with `whitespace-pre overflow-x-auto bg-pane-2/40 border
  border-edge` so wide payloads get a horizontal scrollbar instead of
  wrapping. Also: Engagement-latency panel column headers humanized
  (`product_type`→`Product`, `asset_class`→`Asset Class`, `N`→`Samples`,
  `P50 (s)`→`Median (s)`, `week`→`Week`) — the previously-PARTIALLY-DONE
  rename.
- `trading_corp/web/templates/partials/trade_flow.html` — tile `<div>`
  converted to `<details>` / `<summary>` with `<pre>` body. Default
  browser disclosure marker suppressed via Tailwind arbitrary variant
  (`list-none [&::-webkit-details-marker]:hidden` on the `<summary>`)
  so the only indicator is the custom `▶` chevron rotating with
  `group-open:rotate-90`. Differs intentionally from the
  Engagements-log row pattern (which kept the dual marker to match
  thesis-library precedent on the same screen) — tile UI looked
  weirder with a stray default triangle inside a styled box.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Engagement-log rows on `/research` are click-to-expand inline
  accordions** showing the full `audit_event.payload_json` pretty-
  printed. Multiple rows can be open at once. Backlog item
  "P5 — Research screen: expand-on-click rows in Engagements log
  (PARTIALLY DONE → SHIPPED)".
- **Live trade flow tiles on `/` are click-to-expand** with the same
  pattern. Backlog item "P5 — Live trade flow: expand-on-click tiles
  (PARTIALLY DONE → SHIPPED)". Note: tile collapses on the htmx 5s
  refresh tick — explicitly accepted per spec; localStorage
  state-preservation is out of scope.
- **Engagement-latency panel column headers humanized** for
  Board-facing readability. Backlog item "P5 — Research screen:
  humanize Engagement latency panel column labels (PARTIALLY DONE →
  SHIPPED)".

**Notable code changes (callouts a future Claude shouldn't miss):**
- The two expand patterns differ on the disclosure-marker handling
  (engagement-log keeps the dual marker; trade-flow suppresses it).
  This is intentional and called out in BACKLOG.md. Future polish
  pass: normalize both with a site-wide CSS rule.
- `payload_pretty` key adds modest bandwidth to the SSR responses —
  120 engagements × ~typical payload + 20 trade-flow events × payload.
  Verified comfortably under any sane SSR ceiling; no perf regression
  observed.

**Latent bugs caught + fixed:** none.

**Verification:**
- PID 105xxx → 107062 (restart at 21:44:05 UTC).
- `/research` returns 200 in 2.7s; `/` 200 in 2.6s; `/partials/trade-flow`
  200 in 3ms.
- Content check: `curl http://127.0.0.1:8000/partials/trade-flow | grep -c
  'payload_pretty\|group-open:rotate-90'` → 12 (one per tile pre-htmx-tick).
- Content check: `curl http://127.0.0.1:8000/research | grep -c
  '<details class="px-4 py-2 group"'` → 142 (engagement-log rows + thesis
  library + position-context bundles all use the pattern).
- No new errors in journalctl post-restart aside from the baseline
  Fidelity bot-block + yfinance BTC earnings noise (both pre-existing).

**Inert / dormant on current traffic:** none.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=.pre-dashboard-polish-20260502-2143; BASE=/home/azureuser/trading_corp;
for f in trading_corp/web/templates/research.html trading_corp/web/templates/partials/trade_flow.html trading_corp/web/routes.py trading_corp/web/data.py; do
  mv \$BASE/\$f\$TAG \$BASE/\$f;
done;
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 16:01 UTC — Webhook handlers refactored to return-fast (TV 10s-timeout fix)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** Board reported overnight TV alerts showing
"Webhook delivery failed — request took too long and timed out" on
the 04:00 UTC 4h-bar Cypher signals. Investigation found the webhook
handlers run the broker snapshot + agent.on_alert + research-firm
consult inline before returning HTTP 200, and the research consult
alone can take 12-30s on a multi-expert engagement (verified during
today's 15:31 UTC replay-endpoint test). With auto_execute=false on
both Otter and Cypher today, no QUALIFIED bull alert has hit this
path yet — but the architecture was a latent timeout bomb.
**Backup tag:** `.pre-returnfast-20260502` (on 1 modified file)

**Files deployed (1):**
- `trading_corp/web/webhooks.py` — both handlers (`lord_otter_webhook` + `market_cypher_webhook`) refactored. Synchronous phase now does only validation + the `webhook_received` audit, then dispatches the heavy processing onto a FastAPI `BackgroundTasks` and returns HTTP 200 with `{"status":"accepted","signal":...,"symbol":...}` in well under 200ms. Background processing logic extracted into module-level `_process_lord_otter_alert(...)` and `_process_market_cypher_alert(...)` async helpers, each wrapped in a catch-all that writes an `agent_error` audit row tagged with `phase=background_processing` and Telegram-notifies the Board.

**Features shipped:**
- TradingView's 10s webhook timeout is no longer load-bearing for any downstream work. Even if research consult takes 30s, risk gate stalls, or broker snapshot hangs, TV gets HTTP 200 in <200ms.
- Audit chain unchanged in shape but split across the sync/background boundary: `webhook_received` lands inline (so we have a record even if the background crashes), all subsequent decision rows (`alert_ignored`, `risk_rejected`, `would_have_placed`, `filled`, `execution_error`, etc.) land in the background task.
- New `agent_error` row variant with `phase=background_processing` flag — distinguishes a crash inside the new background helper from the older inline-handler `agent_error` cases.
- Telegram catch-all on background crashes — silent failures impossible.

**Notable code changes:**
- HTTP response shape changed for both handlers. Pre-refactor: `{"status":"would_have_placed", "order_id":"...", "decision":"..."}` (varied per outcome). Post-refactor: uniform `{"status":"accepted", "signal":..., "symbol":...}`. Anything observing TV-callback bodies (we don't, TV doesn't read response bodies) would see the change. The audit log + Telegram surface remain the source of truth, unchanged.
- Existing test `test_push_back_skips_order_and_notifies_board` updated to assert on audit + Telegram side-effects instead of the now-uniform body. The contract that risk_agent isn't called on push_back is preserved — that assertion still passes.
- `test_no_research_firm_falls_through_to_existing_flow` — body assertion changed to `body["status"] == "accepted"`. Negative assertions ("no research_* audit rows", "Telegram NOT called") still hold.
- New tests: `tests/test_webhooks_return_fast.py` (5 tests) pin: (a) HTTP body uniformly "accepted" on valid alerts, (b) webhook_received audit lands during sync phase, (c) outcome audits land in background, (d) background-task crash writes agent_error + Telegram, (e) Cypher handler has same contract.

**Latent bugs caught:** none specific to this refactor — the underlying issue (event-loop-blocking inline processing) was the bug being fixed.

**Verification:**
- PID 102701 → 105xxx after restart, status active.
- md5sum 1/1 file MATCH between local and prod post-scp.
- Live end-to-end: `POST https://trading.jacksumner.com/webhook/tradingview/market-cypher` with this morning's failed alert payload returned `{"status":"accepted","signal":"mc_a_red_diamond","symbol":"BTC/USD"}` in **0.119s** through the full Caddy → FastAPI stack.
- Audit chain post-test:
  - `webhook_received` at 16:00:33 UTC (sync phase)
  - `alert_ignored` at 16:00:34 UTC (background task, 1s later — for the bear-no-position branch)
- 360 tests passing locally (5 pre-existing P2 PMCC failures unchanged).

**Inert / dormant on current traffic:**
- The catch-all background-crash audit + Telegram path is wired but should never fire on healthy traffic. Will surface only on actual exceptions.
- Nothing else inert — both Otter and Cypher webhooks are receiving live traffic; any TV alert exercises the new code path within minutes.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-returnfast-20260502; BASE=/home/azureuser/trading_corp; \
mv \$BASE/trading_corp/web/webhooks.py.\$TAG \$BASE/trading_corp/web/webhooks.py; \
sudo systemctl restart trading-corp
"
```

---

## 2026-05-02 15:31 UTC — Manual research-firm replay endpoint + dashboard button (on-demand consult on past TV signals)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** session conversation — Board wanted to see how the
research firm consult code path would have responded to this morning's
10:54am ET `money_bag_top` alert. Today's filter rejects bear signals
when no position is held, so the consult never fires on those rows.
This adds an opt-in replay path so the existing engagement code can be
exercised against historical signals on demand.
**Backup tag:** `.pre-replay-research-20260502` (on 3 modified files)

**Files deployed (4):**
- `trading_corp/web/data.py` — `_query_division_activity` now exposes audit_event `id` + `signal` on each row dict so the dashboard template can build the per-row replay button.
- `trading_corp/web/routes.py` — new `POST /audit/{audit_id}/replay-research` endpoint. Validates kind is in {webhook_received, alert_ignored, would_have_placed}, calls signal_replay, returns an htmx-swappable HTML fragment (verdict pill + colored rationale).
- `trading_corp/web/templates/division.html` — Recent Activity rows now render a "Send to research →" button on signal-shaped events. htmx POST + inline result swap underneath. Behind Authelia like the rest of the dashboard.
- `trading_corp/agents/research/signal_replay.py` (new) — `synthesize_order_from_payload(payload, audit_event_id=...)` reconstructs a ProposedOrder shape from a TV webhook payload, marks `extra.synthetic=True` so the firm + downstream consumers know it isn't from the live agent path. `replay_signal_research(audit_row, ...)` routes through the existing `consult_research_for_trade_confirmation` helper, writes a `research_replay_completed` audit row tagged with the source audit id. 60s timeout (vs 8s on live path — replay isn't on a live order path, no rush).

**Features shipped:**
- Per-row "Send to research →" button on the per-division Recent Activity panel for `webhook_received` / `alert_ignored` / `would_have_placed` rows. Click → htmx POST → inline result swap with verdict pill (green CONFIRM, yellow CONDITIONAL/TIMEOUT, red PUSH_BACK/ERROR) + the firm's rationale beneath.
- Audit trail: `research_replay_completed` (or `research_replay_failed` on synthesis/consult failure) captures source_audit_event_id, verdict_kind, decision, rationale (truncated 500 char), signal, symbol, alert_price/time. Surfaces on the existing Research screen Engagements log alongside the engagement's other rows.
- Side inference: bear-leaning signal-name fragments (`bear`, `top`, `red_diamond`, `sell_circle`, `spoon_bear`, `money_bag_top`) → side='sell'. Everything else → 'buy'. Synthetic order's qty is fixed 0.01 placeholder — research firm reasons about setup, not size.

**Notable code changes:**
- Discovered + fixed during deploy: `EngagementSpec.requesting_division` is misnamed — it actually expects the strategy/agent slug (`lord_otter`/`market_cypher`), not the broker-account division slug. First request returned `ValidationError` because we passed `coinbase_spot`. Fix: pull `payload.strategy` first, fall back to `audit_row.actor`. **The misnaming itself is a pre-existing schema oddity worth a separate cleanup item** — naming the field `requesting_strategy` (or `requesting_agent`) would prevent future foot-shoots.
- Discovered + fixed: 8s default timeout on `consult_research_for_trade_confirmation` is wired for the live webhook path where speed matters; multi-expert engagements typically take 15-30s end-to-end. Replay isn't on a live path so we pass `timeout_s=60.0` explicitly. First successful replay completed in 12.5s.

**Latent bugs caught + fixed:** see "Notable code changes" above. Both surfaced during the live deploy validation of the new endpoint.

**Verification:**
- PID 102701 → 105xxx (after the timeout-bump scp+restart), status active.
- md5 4/4 files MATCH between local and prod post-scp (final pass after both fixes).
- 19 new tests in `tests/test_signal_replay.py`, all green. Full suite 355 passed (5 pre-existing P2 PMCC failures unchanged).
- Live end-to-end exercise: `POST /audit/614/replay-research` (the 14:54 UTC `money_bag_top` from this morning) returned `verdict=PUSH_BACK decision=skip` in 12.5s. Firm rationale captures (a) it's a synthetic-replay signal, (b) macro neutral with VIX ~17, (c) NFP/FOMC/CPI risk in window, (d) two of three expert dimensions unobserved → insufficient evidence. Audit trail shows full engagement chain: research_engagement_started → research_data_fetch_attempted → research_position_context_emitted → research_expert_completed × N → research_expert_refused × N → research_trade_confirmation_emitted → research_tradeconf_pushback_acted_on → research_replay_completed.

**Inert / dormant on current traffic:**
- The button only renders on signal-shaped audit rows (webhook_received / alert_ignored / would_have_placed). PMCC, Fidelity, and other audit rows don't show the button.
- Not on a live order path. Cannot affect order placement under any code path. Even on a `confirm` verdict, the synthetic order has `extra.synthetic=True` and is never sent to data_exec — the consult result is purely informational.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-replay-research-20260502; BASE=/home/azureuser/trading_corp; \
for f in trading_corp/web/data.py \
         trading_corp/web/routes.py \
         trading_corp/web/templates/division.html; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/trading_corp/agents/research/signal_replay.py; \
sudo systemctl restart trading-corp
"
# research_replay_completed / research_replay_failed audit rows stay
# in the DB after rollback (harmless — just historical records).
```

---

## 2026-05-02 14:56 UTC — would_have_placed Phase C (replay job + dashboard panel) + 0-DTE Terminal-DTE Override calendar refactor + P1 cycle-continuity

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** "lets do 0 dte backlog item and then deploy" — bundled
Phase C of the would_have_placed enrichment with the P0 0-DTE
Terminal-DTE Override refactor (and the P1 cycle-continuity release
folded in).
**Backup tag:** `.pre-phaseC-0dte-20260502` (on 6 modified files)
**New venv dep:** `pandas-market-calendars 5.3.2` (transitive:
exchange-calendars 4.13.2, korean_lunar_calendar 0.3.1, pyluach 2.3.0,
toolz 1.1.0)

**Files deployed (8):**
- `requirements.txt` — added pandas-market-calendars>=4.4.0 (NYSE schedule for half-day / holiday-aware 0-DTE deadline gates).
- `config/strategies.yaml` — new `robinhood_pmcc.zero_dte` block: release_offset_min=60, hard_deadline_offset_min=30, cycle_continuity_extrinsic_threshold=0.15. All three are operationally tunable without a code deploy.
- `trading_corp/main.py` — wired the paper_trade_replay loop alongside the PMCC scan scheduler. Startup catch-up fires before the loop is spawned (mark_pre_phase_a_rows + one immediate replay tick). Loop runs every 900s, cancelled on shutdown. Log lines use f-strings to bypass the RedactingFilter dict-mangling (a separate pre-existing harness bug).
- `trading_corp/web/data.py` — new `paper_trade_summary(db_url, division)` returns 7d/30d/all-time totals (wins/losses/expired/open) + simulated $ P&L per window. pre_phase_a rows excluded from win-rate math, surfaced separately via `n_pre_phase_a`. DivisionViewSnapshot grew a `paper_trade_summary` field.
- `trading_corp/web/templates/division.html` — new "Paper-trade win rate" section above Recent Activity. 3 cards (7d/30d/all) with win % colored green/red, W/L/E counts, sim P&L. Hidden when `totals.all.n == 0` so PMCC/Fidelity divisions don't show empty cards.
- `trading_corp/agents/divisions/pmcc_robinhood.py` — `_terminal_dte_time_release` refactored from hardcoded 15:00/15:30 ET clock to NYSE-calendar-aware close-relative offsets. Added cycle-continuity P1 release path (mark <= threshold AND short_leg_dte == 0 → roll_short, regardless of time). Helper accepts an optional `calendar=` kwarg for test injection. Prompt-rule docstrings (Rules 7 + 4) updated to describe both release paths.
- `trading_corp/agents/paper_trade_replay.py` (new) — walk-forward classifier with conservative same-bar tie-rule (both TP and SL hit in one 1m bar → assume LOSS). Public sync entry, async-native variant, asyncio loop spawner. Default Coinbase ccxt fetcher (paginated, no auth) for OHLC. mark_pre_phase_a_rows helper for the startup catch-up.
- `trading_corp/utils/market_hours.py` (new) — MarketHoursCalendar wrapper around pandas_market_calendars. Memoized per-date close lookups (lru_cache 2048). Graceful fallback when pmcal import fails (every weekday closes at 16:00 ET, weekends closed) — degraded but functional, logged once per process.

**Features shipped:**
- **Phase C of would_have_placed enrichment.** Background replay loop walks paper_trade_record rows where result IS NULL, fetches 1m OHLC bars from Coinbase via ccxt, classifies each row as win/loss/expired (with conservative same-bar tie = loss). Writes result_*, actual_pnl_dollars, actual_r_multiple, bars_to_resolution. 15-min interval; restart triggers immediate catch-up tick.
- **Per-division "Paper-trade win rate" dashboard panel** on /division/{slug}. 3-card layout (7d/30d/all-time) with win rate %, W/L/E counts, sim P&L. Auto-hidden on divisions with no rows. Shows pre-Phase-A row count as a footnote when relevant (5 rows on coinbase_spot today, marked pre_phase_a at startup).
- **0-DTE Terminal-DTE Override is now NYSE-calendar-aware.** Hardcoded 15:00 ET / 15:30 ET thresholds replaced with `close - release_offset_min` / `close - hard_deadline_offset_min` lookups against the NYSE schedule. Half-day closes (e.g. 13:00 ET on day after Thanksgiving) correctly slide the deadline to 12:00 / 12:30 ET. Friday-holiday rotations land the deadline on Thursday's close. Defaults match prior 60/30 minute behaviour.
- **P1 cycle-continuity release.** When a 0-DTE short's mark has decayed to ≤$0.15/share (config knob), force roll_short regardless of time — captures next-cycle premium at today's IV, eliminates post-expiry coverage gap. Operates independently of the time gate; both check short_leg_dte == 0 first.

**Notable code changes:**
- f-string log-formatting in main.py + paper_trade_replay.py for the replay-counts dicts. The harness's RedactingFilter rewrites dict log args into a tuple of keys, which then fails `%s` formatting with TypeError. f-strings sidestep this. **Filing a separate observation:** the RedactingFilter's dict-handling is a pre-existing bug worth a small backlog item — affects any future caller passing a dict via %-style logging. Not a regression, just exposed by Phase C.
- `MarketHoursCalendar.close_time_et` returns tz-aware ET datetimes via `.astimezone(ET)` so DST arithmetic stays correct under timedelta subtraction.
- `_terminal_dte_time_release` now takes optional `calendar=` for test injection. Production path uses `default_calendar()` module-level singleton to avoid re-loading the NYSE schedule per call.
- Test refactor (`tests/test_pmcc_logic.py:_FakeCalendar`) — simple test double for the calendar so existing 7 tests + DST test work without depending on pandas_market_calendars being installed in CI.

**Latent bugs caught + fixed:**
- The "Logging error" `TypeError: not all arguments converted` from the first restart at 14:51 UTC was caught immediately, fix scp'd at 14:55 UTC, restart at 14:56 UTC clean. The replay loop was actually functioning during the broken-logging window — only the count summary failed to render.

**Verification:**
- PID 87416 (Phase B running) → 99920 (first attempt with logging bug) → 100824 (clean fix), status active.
- md5sum 8/8 files MATCH between local and prod post-scp.
- pandas_market_calendars import smoke test on prod: `default_calendar().close_time_et(date(2024, 7, 3))` returns `2026-07-03 13:00:00-04:00` — half-day correctly resolved.
- paper_trade_replay startup catch-up: `{'scanned': 0, 'resolved_win': 0, 'resolved_loss': 0, 'resolved_expired': 0, 'marked_pre_phase_a': 0, 'errors': 0}` — note: marks=0 because the explicit mark_pre_phase_a_rows call before replay_pending_paper_trades_async already marked the 5 historical rows (idempotent inner mark sees 0).
- Database row distribution post-restart: `pre_phase_a: 5` (the 4 Otter + 1 Cypher backfilled rows from Phase B). All have NULL stop or NULL tp_price (Phase A wasn't shipped at their alert times), so they correctly fell through to pre_phase_a.
- Dashboard probe `GET /division/coinbase_spot` returns HTTP 200 with the new "Paper-trade win rate" section rendering and the "5 pre-Phase-A row(s) excluded" footnote present.
- 336 tests passing locally. 5 pre-existing P2 PMCC liquidity-gate failures unchanged (BACKLOG).

**Inert / dormant on current traffic:**
- Replay loop sees no rows to actually classify yet — every paper_trade_record row landed pre-Phase-A. Once new TV-driven `would_have_placed` events fire post-Phase-A (auto-execute is `false` on both Otter and Cypher, so every alert lands here), the loop will start classifying them within 15 min.
- 0-DTE gates only fire when a PMCC short reaches 0 DTE. PMCC scan path is daily 8:30-9:25 ET; the time-of-day gate path will exercise on the next 0-DTE expiration day with an active short. Cycle-continuity P1 path will exercise as soon as a 0-DTE short's mark decays to ≤$0.15/share — could be the same day depending on IV.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-phaseC-0dte-20260502; BASE=/home/azureuser/trading_corp; \
for f in requirements.txt \
         config/strategies.yaml \
         trading_corp/main.py \
         trading_corp/web/data.py \
         trading_corp/web/templates/division.html \
         trading_corp/agents/divisions/pmcc_robinhood.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/trading_corp/agents/paper_trade_replay.py \
      \$BASE/trading_corp/utils/market_hours.py; \
sudo systemctl restart trading-corp
"
# Note: pandas_market_calendars + transitive deps stay installed; harmless,
# can be left or `pip uninstall` if the rollback is permanent.
```

---

## 2026-05-02 05:45 UTC — would_have_placed enrichment Phase B (paper_trade_record table + write-on-emit)

**Commits:** local-only (uncommitted as of deploy time)
**Triggered by:** "execute the plan 1-5" — bundled Phase B of the
would_have_placed enrichment (BACKLOG.md 2026-05-01 P1 entry, Phase B sub-task)
**Backup tag:** `.pre-phaseB-20260502` (on 6 modified files)

**Files deployed (7):**
- `config/strategies.yaml` — added strategy-global `max_hold_seconds`: lord_otter=86400 (24h), market_cypher=604800 (7d). Frozen onto each paper_trade_record at write time so config edits don't retroactively alter past trades.
- `trading_corp/agents/divisions/lord_otter.py` — new `max_hold_seconds` property reading from yaml with 86400 default.
- `trading_corp/agents/divisions/market_cypher.py` — new `max_hold_seconds` property with 604800 default.
- `trading_corp/persistence/db.py` — new `paper_trade_record` table + 2 indexes (`ix_paper_trade_record_strategy_ts`, `ix_paper_trade_record_result`); new `insert_paper_trade_record(record_dict, db_url)` helper using INSERT OR IGNORE on order_id.
- `trading_corp/persistence/models.py` — new `PaperTradeRecord` dataclass + `to_db_row()` + `from_order(order, *, strategy, division, max_hold_seconds)` factory that pulls Phase A trade-card fields out of order.extra and computes expected_loss / rr_ratio.
- `trading_corp/web/webhooks.py` — new module-private `_record_paper_trade(deps, order, strategy, agent)` helper; called inside both Otter and Cypher `would_have_placed` branches (after audit log_event, before Telegram push). try/except wrapped: a write failure logs a WARNING but does NOT break the order flow — audit_event remains source of truth.
- `scripts/backfill_paper_trade_record.py` (new) — idempotent one-shot script that walks `audit_event WHERE kind='would_have_placed'`, joins to `proposed_order.extra_json`, and inserts a paper_trade_record per row. Safe to re-run (INSERT OR IGNORE).

**Features shipped:**
- New SQLite table `paper_trade_record` written on every `would_have_placed` emission. Structured columns for trade specs (entry_reference_price, stop_price, tp_price, tp_r_multiple, expected_loss, expected_gain, rr_ratio) + Phase C-anticipating result columns (result, result_ts, result_price, actual_pnl_dollars, actual_r_multiple, bars_to_resolution) that stay NULL until the future replay job populates them.
- One-time backfill of historical paper trades: 5 pre-deploy rows backfilled (4 lord_otter, 1 market_cypher; first ts 2026-04-30 17:41 UTC, last ts 2026-05-01 02:06 UTC). All pre-Phase-A historical rows have NULL trade-spec fields where Phase A would populate; that's expected and the future replay job will skip them.
- `max_hold_seconds` strategy-global config knob frozen per row at write time. Phase C replay job will use this to decide when to mark `result='expired'` for trades that didn't hit either TP or SL within the window.

**Notable code changes:**
- Schema migration is automatic via `init_db()` `CREATE TABLE IF NOT EXISTS` — service restart applies it. No manual SQL run.
- Failure mode for the new write path is fail-open: try/except in `_record_paper_trade` so a paper_trade_record write error does NOT abort the audit-log + Telegram push. The audit_event row is still source of truth (per CLAUDE.md §1 "audit log writes BEFORE every decision branch").
- INSERT OR IGNORE keying on order_id means the inline write-on-emit path and the backfill script can never collide. Whichever wrote first wins.
- BACKLOG.md entry updated in-tree with Phase A ✅ shipped / Phase B ✅ in-tree-as-of-2026-05-02 / Phase C ⬜ pending status header. (BACKLOG.md is dev-only, not deployed.)

**Latent bugs caught + fixed:**
- None.

**Verification:**
- PID 82701 → 87416, status active
- md5sum 7/7 files MATCH between local and prod post-scp
- `.schema paper_trade_record` returns the full CREATE TABLE + 2 indexes
- Backfill ran clean: `WROTE: scanned=5 inserted=5 skipped_no_order_id=0`
- Row inspection: 4 lord_otter rows + 1 market_cypher row, max_hold_seconds populated correctly per strategy (86400 / 604800), stop_price + expected_loss populated on rows where the order's `extra` had `max_dollar_risk` (pre-Phase-A path), tp_price/expected_gain NULL across all (pre-Phase-A — no TP fields had been written yet at those alert times).
- Dashboard probes: `GET /` HTTP 200, `GET /research` HTTP 200
- 11 new tests in `tests/test_paper_trade_record.py` green; full suite 305 passed (5 pre-existing P2 PMCC scan failures, called out in BACKLOG.md, unchanged)
- Service log post-restart: only baseline errors (Fidelity bot-block + yfinance "No earnings dates for BTC/USD") — same baseline noted in prior deploy_log entries; no Phase B-introduced errors

**Inert / dormant on current traffic:**
- The new `_record_paper_trade` write hook only fires on `would_have_placed` branches (auto_execute=false). Both Otter and Cypher are auto_execute=false today, so every alert hits the new path. NOT inert — exercising on live traffic immediately.
- Phase C replay job code is NOT deployed; result columns will stay NULL until that lands. No-op for now.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-phaseB-20260502; BASE=/home/azureuser/trading_corp; \
for f in config/strategies.yaml \
         trading_corp/agents/divisions/lord_otter.py \
         trading_corp/agents/divisions/market_cypher.py \
         trading_corp/persistence/db.py \
         trading_corp/persistence/models.py \
         trading_corp/web/webhooks.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -f \$BASE/scripts/backfill_paper_trade_record.py; \
sudo systemctl restart trading-corp
"
# Note: rolling back the schema (DROP TABLE paper_trade_record) is
# OPTIONAL — the table will simply sit unused on the rolled-back code.
# Only drop it if you're certain you won't be replaying these rows.
```

---

## 2026-05-02 03:30 UTC — Research firm Phase 1f

**Commits:** `ce15602`, `d61b7ec`
**Triggered by:** "deploy" instruction after Phase 1f UAT passed (22 checks incl. real-LLM smoke)
**Backup tag:** `.pre-1f-20260502-0030` (on 7 modified files)

**Files deployed (13):**
- `trading_corp/agents/llm.py` — _TEMPERATURE_REJECTING_MODELS set; skip temperature for Opus 4.7
- `trading_corp/agents/logger.py` — log_event returns cur.lastrowid
- `trading_corp/agents/research/state.py` — debate_audit_row_id field on EngagementState
- `trading_corp/agents/research/graph.py` — debate_gate node + threading
- `trading_corp/agents/research/synthesis/thesis.py` — debate threading + always-insert driver
- `trading_corp/agents/research/synthesis/position_context.py` — debate threading + risk_flag surface
- `trading_corp/agents/research/synthesis/trade_confirmation.py` — debate threading + tags audit_row_id
- `trading_corp/agents/research/debate_gate.py` (new) — variance/disagreement gate
- `trading_corp/agents/research/experts/debate/__init__.py` (new)
- `trading_corp/agents/research/experts/debate/_base.py` (new) — shared bull/bear runner
- `trading_corp/agents/research/experts/debate/bull.py` (new) — Sonnet
- `trading_corp/agents/research/experts/debate/bear.py` (new) — Sonnet
- `trading_corp/agents/research/experts/debate/judge.py` (new) — Opus, scores quality only

**Features shipped:**
- Bull/bear/judge debate round fires on single-symbol engagements where
  expert variance >= 0.25 OR >= 2 experts disagree on directional_lean
- Two new audit kinds visible in dashboard: `research_debate_invoked`,
  `research_debate_completed`
- Debate context flows into Thesis key_drivers ("debate (gate fired): ..."),
  PositionContext risk_flags ("debate fired: ..."), and TradeConfirmation
  via debate_audit_row_id
- v3 design feature-complete on all 4 product types

**Notable code changes:**
- `agents/llm.py` `_TEMPERATURE_REJECTING_MODELS = {"claude-opus-4-7"}` — extend this set as Anthropic deprecates temperature on more models
- `agents/logger.py` `log_event` signature changed `None` -> `int | None` — backwards-compat for callers that ignore the return

**Latent bugs caught + fixed:**
- Opus 4.7 deprecated `temperature` parameter; judge silently fell back to placeholder scores on every firing pre-fix
- `log_event` always returned None, so `debate_audit_row_id` could never be a real id

**Verification:**
- PID 78397 -> 82701, status active
- 2 PositionContext primes completed end-to-end (Otter 4h + Cypher 24h)
- Graph compiles to 15 nodes including `debate_gate`
- /research dashboard probe HTTP 200, sections present
- 5 Fidelity bot-block + 1 yfinance no-earnings line are baseline (not regressions)

**Inert / dormant on current traffic:**
- Debate gate is on disk + exercising itself but **never fires** in current
  prod traffic. Crypto-only PositionContext engagements (Otter+Cypher prime
  BTC/USD on every restart) have only macro as a valid expert (sentiment
  refuses on crypto). Single-voice panel can't fire. The gate will start
  firing when (a) Otter/Cypher get equity exposure, (b) Board fires a
  Thesis on equity, or (c) PMCC scout TradeConfirmation engagements run
  with multiple experts.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-1f-20260502-0030; BASE=/home/azureuser/trading_corp; \
for f in trading_corp/agents/llm.py trading_corp/agents/logger.py trading_corp/agents/research/state.py trading_corp/agents/research/graph.py trading_corp/agents/research/synthesis/thesis.py trading_corp/agents/research/synthesis/position_context.py trading_corp/agents/research/synthesis/trade_confirmation.py; do \
  mv \$BASE/\$f.\$TAG \$BASE/\$f; \
done; \
rm -rf \$BASE/trading_corp/agents/research/experts/debate \
       \$BASE/trading_corp/agents/research/debate_gate.py
"
```

---

## 2026-05-02 02:13 UTC — routes.py hotfix (research_data_fetch_attempted)

**Commits:** `c29713a`
**Triggered by:** Phase 1d/1e dashboard 500 error post-restart — _summary_for_event
sliced `payload.get('error', '')[:60]` returning `None[:60]` when the key existed
with value None.
**Backup tag:** `.pre-hotfix-fetch-err-20260501-2330`

**Files deployed (1):**
- `trading_corp/web/routes.py` — defensive `(payload.get("error") or "")[:60]`

**Features shipped:**
- Dashboard /research stops returning HTTP 500 when audit log contains
  `research_data_fetch_attempted` rows with `error=None` payloads.
  These rows started landing because Phase 1d's PositionContext prime
  fired real macro-expert engagements that wrote them.

**Verification:**
- Service restart picked up the fix (FastAPI binds routes at startup;
  no hot-reload available)
- /research returns 200 with PositionContext audit trail rendering

---

## 2026-05-01 23:30 UTC — Research firm Phase 1d + 1e bundle

**Commits:** `b145d82` (Phase 1d), `1cb7e70` + `5be2588` (Phase 1e graph + division halves)
**Triggered by:** "deploy" instruction after Phase 1e UAT passed (real-LLM smoke included)
**Backup tag:** `.pre-1d1e-20260501-2330` (on 9 modified files)

**Files deployed (14):**
- `trading_corp/agents/research/graph.py` — Layer 1 + new emit nodes
- `trading_corp/agents/research/schemas.py` — new audit-kind constants
- `trading_corp/agents/divisions/lord_otter.py` — _fetch_position_context, on-alert hook, configured_symbols, last_position_context, **TP fields in `_build_order` (Phase A scaffolding)**, division consult call
- `trading_corp/agents/divisions/market_cypher.py` — same shape (24h horizon), TP fields, consult call
- `trading_corp/main.py` — startup-of-day prime task
- `trading_corp/web/webhooks.py` — TradeConfirmation consult call between on_alert + risk gate; **Phase A `_format_trade_card` shared renderer for would_have_placed pushes**
- `trading_corp/web/routes.py` — position_contexts view
- `trading_corp/web/templates/research.html` — collapsible PositionContext audit trail
- `config/research.yaml` — `trade_confirmation` block (timeout + kill switch)
- `trading_corp/agents/research/synthesis/position_context.py` (new)
- `trading_corp/agents/research/synthesis/trade_confirmation.py` (new)
- `trading_corp/agents/research/position_context_cache.py` (new)
- `trading_corp/agents/research/prime.py` (new)
- `trading_corp/agents/research/trade_confirmation_consult.py` (new)

**Features shipped:**
- PositionContext engagement type emits via the graph + audit row +
  dashboard view
- Pre-emptive cache for PositionContext (TTL-gated agent_state rows,
  per-division horizons in research.yaml)
- Startup-of-day prime task on every restart populates the cache for
  configured symbols
- Otter + Cypher consume cached PositionContext on alert
  (state.last_position_context; not yet gating behavior)
- TradeConfirmation consult on every Otter/Cypher webhook between
  agent.on_alert and the risk gate (8s hard timeout, fail-open)
- push_back verdict triggers Telegram notify with rationale; conditional
  applies SuggestedModifications transparently
- **Phase A enrichment of would_have_placed pushes** — `_format_trade_card`
  shared renderer outputs full trade card (entry, stop, take-profit,
  R:R, expected P&L) for both Otter and Cypher
- TP fields populate in order.extra: take_profit_price, tp_basis,
  tp_r_multiple, tp_distance_dollars, tp_distance_pct,
  expected_gain_if_tp_hit, expected_loss_if_stopped, entry_reference_price

**Notable code changes:**
- 4 new audit kinds shipped: research_tradeconf_pushback_acted_on,
  research_modifications_applied, research_tradeconf_timeout,
  research_tradeconf_error
- WebDeps already had `research_firm` field — wiring just needed main.py
  to populate it after build_research_firm_deps runs

**Verification:**
- PID change confirmed
- 2 PositionContext engagements completed (Otter 4h, Cypher 24h)
- agent_state rows present for both divisions
- Dashboard initially 500'd on _summary_for_event (latent bug, hotfixed
  separately — see 2026-05-02 02:13 entry)

**Inert / dormant on current traffic:**
- TradeConfirmation consult fires on every Otter/Cypher alert, but most
  alerts in current paper-mode pre-restart audit log are `alert_ignored`
  (bias not set). First webhook that produces an order will exercise
  the consult.

---

## 2026-05-01 (early, no precise timestamp recorded) — Bulk-track scaffolding

**Commits:** `606254e` (and earlier commits unbundled into the bulk-track)
**Triggered by:** Pre-existing trading_corp tree was untracked; bulk-commit added it to git
**Backup tag:** n/a (was in place before tracking started)

**Status:** Best-effort reconstruction — pre-deploy-log discipline.

**Features shipped (already on prod via earlier ad-hoc deploys):**
- Phase 1a-1: CandidateRecommendation engagement graph
- Phase 1a-2: PMCC scout integration with extended-outage notify
- Phase 1b: Thesis ad-hoc + dashboard library
- Phase 1c: Real Fundamental + Sentiment experts (yfinance-backed)
- Holdings table simplification (e14903b)
- PMCC roll history + crypto positions surfacing (b70b6a3, a208f8d)

**Inert observations:**
- Several BACKLOG.md items reference scaffolding that was already
  in-tree at bulk-track time (e.g. take_profit yaml blocks for Otter+Cypher,
  TP-field code paths in _build_order). Some BACKLOG entries describing
  these items have been left as P1 because while the CODE was there,
  the integration into would_have_placed pushes wasn't necessarily
  exercised. Future deploys touching this area should re-verify before
  treating BACKLOG as gospel.

---

## 2026-05-03 16:25 UTC — UI grouping by investment type + Fidelity Individual deactivation + BitUnix placeholder + Coinbase Futures STANDBY

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Sun 2026-05-03 BitUnix Futures vision conversation. User decided crypto split (Coinbase spot = BTC long-only; BitUnix futures = BTC/SOL/ETH bidirectional leveraged; Coinbase Futures → STANDBY) and asked for the dashboard to organize by investment type (Individual / Crypto / Retirement) instead of by broker (Robinhood / Fidelity / Coinbase). Full vision in `~/.claude/.../memory/trading_corp_bitunix_vision.md`. This deploy ships the UI reorg only — phased BitUnix broker build (Phase 1+) is gated on B.4 flag flip Mon.
**Backup tag:** `.pre-inv-type-ui-20260503-1622`

**Files deployed (6 modified):**

- `config/divisions.yaml`:
  - `fidelity_individual` set to `enabled: false` — division deactivated per user decision (option (b) of three: hide / deactivate / delete). YAML entry retained as deadcode for cheap revival. Pre-deactivation safety: account had 0 positions per dashboard.
  - `coinbase_futures` gains `standby: true` flag — UI-only flag, broker init unchanged (still registered, still PaperBroker-wrapped).
  - **NEW** `bitunix_futures` division added — `broker: bitunix`, `account_filter: futures`, `intent: aggressive`, `standby: true`. No broker adapter exists yet; main.py logs WARNING "Unknown broker family 'bitunix'" at startup (expected); hydration falls through to `status='not_wired'`.
- `trading_corp/utils/divisions.py`:
  - Renamed `BrokerGroup` → `InvestmentGroup`, `group_by_broker` → `group_by_investment_type`. New helper `classify_investment_type(d)` maps each division to "individual" / "crypto" / "retirement" using rule: `intent=='retirement'` → retirement; `broker in {coinbase, bitunix}` → crypto; else individual.
  - `_BROKER_ORDER`/`_BROKER_LABELS` replaced with `_INVESTMENT_TYPE_ORDER` (`individual`, `crypto`, `retirement`) and `_INVESTMENT_TYPE_LABELS`. Added `_CRYPTO_BROKERS = {coinbase, bitunix}` for the classifier.
  - **NEW field** `Division.standby: bool = False` (loaded from YAML's `standby` key).
  - Updated `__all__` exports.
- `trading_corp/web/data.py`:
  - Import line updated to new symbol names.
  - `CommandCenterSnapshot.broker_groups: list[BrokerGroup]` → `investment_groups: list[InvestmentGroup]`.
  - Aggregation loop updated.
- `trading_corp/web/templates/home.html`:
  - `{% for grp in snap.broker_groups %}` → `{% for grp in snap.investment_groups %}`.
  - Status badge gains conditional: if `d.standby`, render orange/warn "STANDBY" badge instead of the online/offline/not_wired status badge. Standby is exclusive (replaces, not adds-to, the status badge).
- `trading_corp/web/templates/partials/stat_cards.html`:
  - Variable rename + label change "{N} brokers" → "{N} groups" on the total-equity stat card.
- `trading_corp/comms/telegram_commands.py`:
  - `/status` Telegram message: "*By broker*" section header → "*By investment type*". Emoji map updated: 💼 individual, 🪙 crypto, 🛡 retirement.

**Features shipped (load-bearing for future "is X done?" checks):**
- **Investment-type grouping on /command-center**: dashboard renders three groups in fixed order (Individual / Crypto / Retirement) replacing the prior broker-grouped layout. Each group shows aggregate equity + pnl. Per-group counts visible in stat cards.
- **STANDBY badge UI primitive**: any division with `standby: true` in YAML renders an orange STANDBY badge instead of online/offline. Currently used by Coinbase Futures + BitUnix Futures.
- **Fidelity Individual deactivated end-to-end**: not loaded by `load_divisions()`, no broker registered, not in dashboard, not in /status Telegram. Removable cheaply via YAML `enabled: true` flip if needed.
- **BitUnix Futures placeholder card**: visible in Crypto group with STANDBY tag and equity = "—" (not_wired). Card link → /division/bitunix_futures (will 404 cleanly until division-page hydration handles it; not exercised today).
- **Telegram /status investment-type view**: replaces the broker-aggregate table.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **STANDBY is UI-only.** Setting `standby: true` does NOT disable order routing or broker registration — Coinbase Futures is still registered as a paper-exec broker today. Behavioral disable for Coinbase Futures comes later (per BitUnix vision: keep $-balance reads, drop order path; manual promote later). Don't assume STANDBY === "no orders possible" until that follow-up ships.
- **`broker: bitunix` is unknown to main.py's broker dispatch.** The startup WARNING is harmless but if anyone adds a strict-mode broker check, that warning becomes a fatal. Phase 1 of BitUnix build will register either a real or paper BitUnix broker keyed by `bitunix_futures` slug.
- **`classify_investment_type` is a pure mapping function**, not stored on Division. If you add a new broker family, decide in `_CRYPTO_BROKERS` set whether it's crypto or individual. New retirement-family criterion would need a different rule.
- **Telegram /status emoji map is keyed by group key**, not broker name anymore (`individual`/`crypto`/`retirement` not `robinhood`/`coinbase`/etc.). If you reuse this code path, mirror the new keys.

**Latent bugs caught + fixed (if any):**
None caught/fixed in this deploy.

**Verification:**
- Local smoke test (`python -c "from trading_corp.utils.divisions import ..."`): 8 enabled divisions, 3 groups in correct order: Individual=`[robinhood_pmcc, robinhood_joint, fidelity_joint]`, Crypto=`[coinbase_spot, coinbase_futures, bitunix_futures]`, Retirement=`[robinhood_ira, fidelity_401k]`. Standby flag parses correctly on `coinbase_futures` and `bitunix_futures`.
- Local browser render at `localhost:8000` confirmed by user — three groups render with correct labels, BitUnix shows STANDBY, Coinbase Futures shows STANDBY, no Fidelity Individual card. (Local test bypassed Fidelity via blanked `.env` for fast startup; restored after.)
- Prod restart 16:25:19 UTC; web bound at 16:25:57 UTC (38s); `/healthz` returned 200 OK in 1.7ms.
- Expected `WARNING trading_corp.main: Unknown broker family 'bitunix' for division bitunix_futures` confirmed in journalctl.
- User confirmed visual layout in browser at `https://trading.jacksumner.com`.

**Inert / dormant on current traffic (if any):**
- **BitUnix Futures division**: card visible but division has no broker adapter. Hydration marks `not_wired`. Routes to /division/bitunix_futures will 404 or render with empty data; not exercised today. Phase 1 (broker bring-up + KV migration of section 5c keys) ships post-B.4.
- **Coinbase Futures STANDBY**: badge is purely cosmetic in this deploy. Broker still registered, still order-capable in non-paper modes (we're in PAPER, so moot). Order-path disable comes in a follow-up deploy when the user decides to flip the behavior.
- **Fidelity broker bot-detection error surfaced post-restart**: separate pre-existing flakiness, NOT caused by this deploy. Fidelity Joint + Fidelity 401(k) failed broker connect with "Fidelity rejected the login session... Cache wiped — wait 5-10 min and restart." UI shows them as offline/not_wired until next successful Fidelity login. No data loss; resolves on a future restart.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-inv-type-ui-20260503-1622; BASE=/home/azureuser/trading_corp
for f in config/divisions.yaml \
         trading_corp/utils/divisions.py \
         trading_corp/web/data.py \
         trading_corp/web/templates/home.html \
         trading_corp/web/templates/partials/stat_cards.html \
         trading_corp/comms/telegram_commands.py; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
sudo systemctl restart trading-corp
"
```

---

## 2026-05-03 17:54 UTC — BitUnix Futures Phase 1: read-only broker + KV migration

**Commits:** local-only (uncommitted at deploy time)
**Triggered by:** Sun 2026-05-03 user greenlight to wire up BitUnix Phase 1 (per `~/.claude/.../memory/trading_corp_bitunix_vision.md` Phase 1: read-only standby). User decided to ship Phase 1 *before* B.4 flag flip — the original "post-B.4" sequencing was conservative; Phase 1 doesn't touch the HITL approval flow, so independent ship is safe. The 16:25 UTC UI grouping deploy was cosmetic; this deploy actually wires the BitUnix broker so the placeholder tile shows real account data.
**Backup tag:** `.pre-bitunix-phase1-20260503-1744` (on the 2 modified files; bitunix.py is new — no backup)
**KV migration:** `BITUNIX-FUTURES-API-KEY`, `BITUNIX-FUTURES-API-SECRET` uploaded to `kv-tc-vtwbowt3wtkpy` via targeted `az keyvault secret set` (NOT the full `scripts/upload_secrets_to_keyvault.ps1` — that would clobber prod's divergent LORD-OTTER-WEBHOOK-SECRET / MARKET-CYPHER-WEBHOOK-SECRET values per `trading_corp_prod_ops.md`). Secret values read from `.env`, never echoed to the conversation.

**Files deployed (3 — 1 new, 2 modified):**

- **NEW** `trading_corp/brokers/bitunix.py` (~290 lines):
  - `BitunixBroker(Broker)` — read-only Phase 1 broker
  - `_sign(api_key, api_secret, query, body)` helper — SHA256-double-sign per BitUnix docs (`https://www.bitunix.com/api-docs/futures/common/sign.html`): `digest = SHA256(nonce + ts + key + sortedQuery + body)`, then `sign = SHA256(digest_hex + secret)`. Headers: `api-key`, `sign`, `nonce` (UUID4 hex no hyphens), `timestamp` (ms). No passphrase (BitUnix doesn't use one — `.env`'s `BITUNIX_FUTURES_PASSPHRASE` field is unused).
  - `connect()` — opens httpx async client + smoke-checks via initial snapshot. Failures log a warning but don't raise — hydration catches them later. Stub mode if creds missing (returns zeros instead of failing).
  - `snapshot()` — sums account balance across stablecoin margin coins (`USDT`, `USDC`). Per-coin equity = `available + frozen + margin + transfer + crossUnrealizedPNL + isolationUnrealizedPNL + bonus`. Position list fetched once (margin-coin-agnostic). Verified $2500 reconciles against BitUnix UI (USDC: $1250 available + $1250 transfer; USDT empty).
  - `quote(symbol)` — public `/api/v1/futures/market/tickers?symbols=<sym>` endpoint. No auth.
  - `place_order` / `cancel_order` — raise `NotImplementedError` as a Phase 1 backstop. In PAPER mode (current prod state) these are never reached — PaperExecutionBroker routes orders to PaperBroker. The raise only fires if someone constructs an unwrapped BitunixBroker in LIVE mode, which doesn't happen until Phase 4.
  - Endpoints: `GET /api/v1/futures/account?marginCoin={coin}`, `GET /api/v1/futures/position/get_pending_positions`, `GET /api/v1/futures/market/tickers?symbols=...`. Base URL `https://fapi.bitunix.com`.
- `trading_corp/utils/secrets.py`:
  - Added `BITUNIX_FUTURES_API_KEY` / `BITUNIX_FUTURES_API_SECRET` to `_SECRET_KEY_NAMES` (so values get redacted from logs by `RedactingFilter`).
  - Added `bitunix_futures_api_key` / `bitunix_futures_api_secret` fields to `Secrets` dataclass.
  - Added both env-var names to `expected_env_vars` for Key Vault loader.
  - `load_secrets()` reads both via `_env(...)`.
  - **No** passphrase field — BitUnix's signing uses pure SHA256, not HMAC+passphrase like Coinbase legacy.
- `trading_corp/main.py`:
  - **NEW broker family branch** `if family == "bitunix":` mirroring the Coinbase pattern. Constructs `BitunixBroker(api_key=secrets.bitunix_futures_api_key, api_secret=secrets.bitunix_futures_api_secret)`. In PAPER mode wraps in `PaperExecutionBroker` so snapshots use real BitUnix data while orders simulate via `PaperBroker`. In LIVE mode (not currently exercised) returns the unwrapped real broker — but `place_order` raises until Phase 4 ships.

**Features shipped (load-bearing for future "is X done?" checks):**
- **BitUnix Futures division now reads real account data on prod.** Dashboard tile in Crypto group shows live equity ($2,500.00 confirmed against BitUnix UI), live position count (0 currently), STANDBY badge stays.
- **Real BitUnix API auth working from Azure VM IP.** Unlike Fidelity, BitUnix's API does NOT IP-block the datacenter address. Confirmed by successful snapshot from `tc-prod-vm` at 17:54:47 UTC. (Important contrast for the Fidelity P1 BACKLOG item.)
- **Multi-margin-coin balance aggregation.** Sums USDT + USDC futures balances. BTC/ETH-margined balances are deferred (need quote conversion to USD).
- **Phase 1 read-only enforcement.** `place_order` / `cancel_order` raise `NotImplementedError` on the unwrapped broker as a defensive backstop until Phase 4. PAPER mode wrapping insulates the live signal path.

**Notable code changes (callouts a future Claude shouldn't miss):**
- **`transfer` field is additive in BitUnix equity math.** Initially I assumed `transfer` was a duplicate view of `available` (i.e. "available to transfer out"). User-confirmed against UI: `available + transfer = total wallet balance`. The two are independent components. Keep it summed.
- **BitUnix supports many margin coins; per-coin queries required.** No bulk endpoint exists. Phase 1 sums stablecoins only (USDT, USDC) and treats them 1:1 USD. If user moves funds to BTC/ETH margin, the dashboard will under-count until a quote-conversion path is added.
- **Connect-time smoke check is best-effort, not fatal.** If BitUnix returns 401 or rate-limits during boot, broker stays registered with stub data and hydration catches the error later. No restart loop.
- **Snapshot is sequential + slow (~37s observed).** Three sequential API calls (account x 2 coins + positions). Future polish: parallelize via `asyncio.gather`. Not blocking — happens at startup, not per-request.
- **No live order capability.** `place_order` raises in unwrapped form. PAPER wrapping makes orders flow to `PaperBroker`. Phase 4 will replace the raise with real BitUnix order placement (gated on stop-loss strategy + conviction → leverage map per the vision memo).
- **KV migration was targeted, not full upload.** `scripts/upload_secrets_to_keyvault.ps1` uploads ALL .env values to KV — that would clobber `LORD-OTTER-WEBHOOK-SECRET` and skip the divergent `MARKET-CYPHER-WEBHOOK-SECRET` per `trading_corp_prod_ops.md`. Direct `az keyvault secret set` was used for just the 2 BitUnix keys.

**Latent bugs caught + fixed (during this session):**
- **`marginCoin=USDT` alone misses USDC funds.** Initial snapshot returned $0 because user's $2500 was in USDC, not USDT. Found via raw-API probe across margin-coin variants. Fix: loop over `_STABLE_MARGIN_COINS = ("USDT", "USDC")` and sum.
- **`transfer` field omitted from equity.** Initial formula = `available + frozen + margin + crossUPnL + isoUPnL + bonus`. Returned $1250 instead of expected $2500. User confirmed `transfer` is additive (in-transit balance crediting the wallet, not a duplicate of `available`). Added to formula.

**Verification:**
- Local smoke test (`python -c ...`): BitunixBroker connects, equity = $2500.00, cash = $1250.00 (available across both coins), 0 positions. Quote endpoint returns BTCUSDT live price ($78,690 at test time).
- Local browser render at `localhost:8000` confirmed by user — BitUnix tile in Crypto group shows EQUITY $2,500.00 with STANDBY badge.
- Prod restart 2026-05-03 17:54:09 UTC; web bound at 17:54:47 (~38s); `/healthz` returned 200 OK in 1.2s.
- Prod journalctl confirmed: KV pulled `BITUNIX-FUTURES-API-KEY` + `BITUNIX-FUTURES-API-SECRET` at 17:54:10; `Registered paper-exec broker for division=bitunix_futures (paper=True)` at 17:54:11; `BitunixBroker connected (account=bitunix-futures, equity=$2500.00, 0 positions)` at 17:54:47.
- "Unknown broker family 'bitunix'" WARNING from the 16:25 UTC deploy is now GONE — confirmed absent in post-restart journalctl.
- User confirmed visual at `https://trading.jacksumner.com`.

**Inert / dormant on current traffic:**
- **No signal fan-out to bitunix_futures division.** Per the autonomous-division vision, signals should reach every division and let each decide; today's signal-routing only sends Otter/Cypher to `coinbase_spot`. So bitunix_futures gets snapshot calls (for the dashboard) but never receives `place_order` calls — even in PAPER. Phase 3 (division-entry filters + signal fan-out) ships that.
- **`place_order` / `cancel_order` raise paths are untested in prod.** They never trigger because of the PaperExecutionBroker wrapping + lack of fan-out. If you remove the wrapper or add fan-out, the raise becomes a real failure mode — Phase 4 will replace with real implementation.
- **BTC/ETH-margined balances not summed.** If user moves funds out of stablecoin margin into crypto-margin, dashboard will under-count until quote-conversion lands.
- **Snapshot timing is slow.** ~37s of three-sequential-API-calls cost at boot. Doesn't affect runtime (snapshots are cached + only re-pulled on dashboard load); just a startup-latency observation for future polish.
- **Coinbase Futures is still order-capable behind STANDBY badge.** Independent from BitUnix Phase 1; tracked as a follow-up to actually disable the order path.

**Rollback recipe:**
```bash
ssh azureuser@trading.jacksumner.com "
TAG=pre-bitunix-phase1-20260503-1744; BASE=/home/azureuser/trading_corp
for f in trading_corp/utils/secrets.py trading_corp/main.py; do
  mv \$BASE/\$f.\$TAG \$BASE/\$f
done
rm -f \$BASE/trading_corp/brokers/bitunix.py
sudo systemctl restart trading-corp
"
# Optionally also remove KV secrets if you want a true Phase-0 state:
#   az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name BITUNIX-FUTURES-API-KEY
#   az keyvault secret delete --vault-name kv-tc-vtwbowt3wtkpy --name BITUNIX-FUTURES-API-SECRET
# (KV secrets being present is harmless if the broker code is gone — main.py
# falls back to the "Unknown broker family" warning path.)
```
