# BitUnix full-surface health check — 2026-05-28 ~22:30 UTC

**Mandate:** after ~36h of changes (PA 2-of-3 + $PnL fix + Phase 2 telegram notifier),
verify everything shipped is executing cleanly and the data flowing into
`paper_trade_record` + `audit_event` is clean and complete for backtesting use.
**Read-only**; zero prod/config/DB changes; all findings verified against actual rows.

## VERDICT: 🟢 GREEN

Everything firing, $PnL writing correctly, audits complete, validator data clean,
no bitunix errors. Backtesting data for the bitunix surface is **clean and complete**.
One item is a *confirmation* not a defect: the Phase 2 Telegram messages are
prod-side clean but await operator phone-confirmation (the GREEN bar explicitly
accommodates this). Three non-bitunix adjacent findings flagged at bottom.

---

## Service health
- PID **1625233**, ActiveState=active/running, **NRestarts=0**, uptime since **04:44:18 UTC**.
- ⚠️ Prompt premise "last restart ~03:53" is **stale**: the 03:53 notifier deploy was followed
  by a **04:44 k3 sports-skip restart** (touched only `kalshi_copy_trader.py`). The notifier
  wiring survived intact. Current uptime is from 04:44, not 03:53.
- healthz `{"status":"ok","mode":"PAPER"}`. DB 830 MB (was 772 MB at the 02:5x backup — normal growth).
- Journal scan since 03:53: **zero bitunix/replay/notifier errors** (every replay tick `errors:0`).
  NameError 'wallet' (10×) is **only** from the old pre-k3 PID 1619590 — none from current 1625247 → k3 fix confirmed live.

## Trade firing (PA 2-of-3 effect)
- **8 fires** since the 23:18 PA deploy: **6 win / 2 loss / 0 expired / 0 open** (all resolved).
- ~23.2h window → **≈8.3 fires/day** vs **0.75/day** pre-deploy baseline (≈11×), under the ~15/day replay upper bound — in the expected range.
- R-multiples: min −1.0, max +1.332, **avg +0.411**; net **+$0.017** (tiny absolute $ = small paper sizing + low BTC ATR). Small n — not a victory declaration.

## $PnL persistence (Phase 1)
- Score-path rows since Phase 1 deploy with `expected_gain` NULL = **0** ✓ (fix working).
- Bitunix `result='win' AND actual_pnl_dollars=0/NULL` = **0** ✓.
- Only zero-PnL bitunix rows = **3 expired** (correctly zero, no fills) ✓.
- All **7 backfilled rows** hold their corrected non-zero values, matching deploy_log to the cent ✓.

## Phase 2 Telegram acceptance (prod-side PASS, phone-confirm pending)
- **5 post-restart resolutions** (result_ts ≥ 03:53): 99d62e04 loss 04:02, 0acb8e5b win 05:06, aaaefb0f win 13:44, 496901ae loss 16:21, 5897a0ae win 19:30 (+ a TP1 partial-fill tick at 05:19).
- `telegram_notification_failed` audit rows (all-time) = **0** ✓.
- "lifecycle notify drain failed" journal warnings = **0** ✓.
- The 04:02 loss was caught live by the 03:53-wired notifier; the rest by the post-04:44 notifier (04:44 catch-up resolved 0 → nothing silently backfilled).
- **OPEN:** operator confirms the `📄 [PAPER]` messages landed on the phone — the one thing prod cannot verify.

## Audit-kind integrity (backtesting completeness)
Funnel matches replay predictions almost exactly:

| kind | count | note |
|---|---|---|
| bitunix_score_decided | 413 | ~423/day (a bit under 500-1000 est; = TV alert volume, not a defect) |
| pa_validation_decision | 297 | passed/failed arrays fully populated ✓ |
| htf_gate_decision | 55 | = PA passes → **PA pass rate 18.5%** (replay predicted 18.4%) ✓ |
| trade_plan_decision | 25 | 17 fee-floor-rejected + 8 placed → **68% reject** (predicted ~70%) ✓ |
| position_sl_update | 11 | TP fills + SL moves |
| would_have_placed (bitunix) | 8 | = 8 fires = 8 paper_trade_record rows (exact reconciliation) ✓ |

PA payload sample (5 rows) — clean structure for backtesting: `strategy, division, trigger_signal, trigger_source, score_side, score_tier, decision, passed[], failed[], reason, mode`.

## PA 2-of-3 validator-pair distribution (early read — window still open)
55 PA passes:

| passed set | n | % |
|---|---|---|
| vwap + structure | 23 | 41.8% |
| vwap + volume | 14 | 25.5% |
| volume + structure | 12 | 21.8% |
| all three (3/3) | 6 | 10.9% |

- **`structure_alignment` contributes to 41/55 passes (74.5%)** — NOT dead-weight.
  **Refutes** the open hypothesis ("if structure never contributes, replace the 4h check with 15m/30m"): do **not** pursue that structural change.
- 49/55 passes are exactly-2 (only 6 are 3/3) → the 2-of-3 loosening is doing real work; 89% of passes would've been rejected under the old `require_all`.
- Interim read (~36h); the 2026-06-03 window-close is still the deciding measurement.

## Database integrity
- `agent_error` / `*error*` audit kinds since deploy = **0** ✓.
- Malformed `payload_json` (json_valid=0) in window = **0** ✓.
- Duplicate `order_id` in paper_trade_record = **0** ✓.
- Bitunix `extra_json`: 8 rows, 0 null, 0 malformed ✓.
- Audit growth ~64K rows/23.4h (~66K/day, dominated by kalshi scan strategies) — normal.

## Known anomalies (status)
- **main.py drift** (tasty_options 94b3129 never deployed): STILL PRESENT — prod md5 `cacc46ed…`, 0 tasty_options refs. Unchanged. (P3)
- **strategies.yaml** missing `tasty_options:` block: STILL ABSENT. Unchanged. (P3)
- **Equity reconciliation:** `auto_execute: false` everywhere → bitunix is structurally paper-only; 0 real fills; no bot-side accounting mismatch is possible. External equity moves confirmed not bot-driven. ✓

## Adjacent findings (NON-bitunix — out of scope, flagged for awareness)
1. **Robinhood 401 Unauthorized flood** (~2000 lines/18h): a broken RH integration's auth is dead, spamming the journal. Not bitunix; hygiene/observability noise.
2. **tastytrade SDK logging TypeError** (91×): `streamer.py:434 logger.debug("received: %s", message)` chokes on a `%` in a websocket payload. Third-party SDK; benign.
3. **11× `sqlite3.OperationalError: database is locked`** — all on the **polymarket_copy_trader / pmcc** write path (`set_agent_state`, `log_proposed_order`, pmcc `log_event`), clustered in burst windows (16:39). These writes are **not retried** → dropped rows on the polymarket side. **No bitunix write was dropped**, but bitunix shares the same `logger.py log_event` writer, so this is a latent risk to data completeness under heavier contention. Worth a watch-item / possible WAL-or-retry fix (separate from bitunix).
