# CP4 — Integration / live shadow

**Status:** complete. **Shadow only — 0 real orders** (executor `dry_run=True`; the V2 POST is
statically unreachable). Holding at the CP4 gate; CP5 not started. No config/main.py wiring. Shared
files (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`) byte-unchanged. Every
claim → paste or `file:line`. Two things did NOT happen in the live window and are stated plainly
(§5, §7) — not synthesized.

---

## 1. The loop (`trading_corp/agents/strategies/poly_kalshi_copy_trader.py`)
`PolyKalshiCopyTrader` polls the 4 whales → new action → CP1 matcher → CP2 order → CP3 guardrails →
shadow log. `dry_run` executor; NOT imported by main.py (no side effects). 9 unit tests + a live run.

## 2. Incremental detection — offset-5000-cap safe
Each poll fetches only the **newest page** (`offset=0`, small limit) and emits rows past a per-whale
high-water `timestamp`; cold start seeds the mark **without emitting** history. We never deep-page
toward the 5000 offset — new actions between 5s polls are always on page 0. Proven:
- `test_only_new_actions_emit_and_offset_is_zero` — asserts `set(client.offsets) == {0}` (never paged).
- `test_cold_start_seeds_without_emitting`, `test_redeem_rows_not_emitted`.
The offset-5000 error two whales hit in CP1 was from **deep back-paging**; the live loop never does
that, so the cap is irrelevant to detection.

## 3. 429 / Cloudflare backoff
Logic proven deterministically (real sleeps patched out):
- `test_backoff_triggers_then_recovers` — two 429s → backs off twice → recovers, seeds mark.
- `test_fetch_giveup_returns_empty_no_crash` — exceeds the schedule → returns `[]`, loop continues.
**Live:** over 122 polls / 661s (~490 requests across 4 whales at 5s), Poly returned **0 rate-limits**
→ `backoff_events == []`. Backoff did NOT trigger live in this window (rate/time-dependent; the
provenance's 429s were earlier the same session). I am not claiming live backoff evidence — the
behavior is proven by the unit tests above.

## 4. Day-rollover
`test_day_rollover_resets_daily_counter` — boot initializes the day key (no reset); when the UTC day
key changes, `_deployed_usd` resets to 0.0. (`_rollover_if_needed`, poly_kalshi_copy_trader.py.)

## 5. [G-slip] LIVE book fetch — works + fail-closed
- **Fetch works** (`cp4_01_slip_live.py`, real open books): `LADCOL-LAD yes_ask=0.62/bid=0.56`,
  `LADCOL-COL 0.40/0.36`, `WSHTEX-WSH 0.44/0.40`. Against each REAL book, whale base 1c under ask
  (slip 1c<2c) → `DRY_RUN_would_place`; 10c under ask (slip 10c>2c) → **`blocked_slippage`**.
- **Fail-closed** (`test_gslip_fail_closed_live_no_quote`): live mode + no quote →
  `blocked_slippage_no_quote`, and the V2 POST is asserted **never reached** (`posted == []`).
- **Fetch exception caught** (`test_loop_quote_fetch_exception_is_caught`): a raising quote_fn →
  quote `None`, loop does not crash.
- Note: settled/closed games return no book → `None` → in live that fail-closes (correct: never
  market-order a settled game). That is why the backlog (past-game) shadow entries show `quote=null`.

## 6. [G-halt] daily-loss AUTO-detection
`test_ghalt_autodetect_fires_persist_halt_and_blocks`: `record_realized(-6)` → within cap;
`record_realized(-5)` → cumulative -11 ≤ -10 cap → **calls `StrategyState.persist_halt`** (the same
primitive RiskAgent uses); `from_persistence(...).halted` becomes True; a subsequent `submit` →
`blocked_halt`. So detection→halt→enforcement is proven end-to-end, not just a manual halt.

## 7. Live shadow run (`shadow_out.json`) — the deliverable
122 polls / 661.6s @ 5s. **8 shadow entries, all cold-start backlog (real recent whale actions).
0 NEW actions fired during the window** — stated plainly, not synthesized. End-to-end on the real
backlog:
```
SDTrading  BUY 'San Francisco Giants' mlb-col-sf-2026-08-16      -> matched 1.0 -> KXMLBGAME-26AUG161605COLSF-SF -> DRY_RUN_would_place (bid x3 @0.5700)
SDTrading  BUY 'Over'   mlb-col-sf-...-total-7pt5                 -> total -> skip_non_ml
xifutloong3 BUY 'Miami Marlins' mlb-mia-cin-2026-08-15           -> matched 1.0 -> KXMLBGAME-26AUG151840MIACIN-MIA -> DRY_RUN_would_place (bid x4 @0.5200)
xifutloong3 BUY 'Miami Marlins' mlb-mia-cin-2026-08-15 (2nd fill)-> matched 1.0 -> SAME key 1fecca4a -> suppressed_duplicate   <- dedup on REAL live data
monkeymashingke BUY 'Lois Boisson' wta-boisson-bencic (tennis)   -> non_mlb -> skip_non_ml  (x2)
0x0x23kjookhai  BUY 'Sentinels'   lol-ly-sen (League of Legends) -> non_mlb -> skip_non_ml  (x2)
```
- **2 would-place, 1 suppressed_duplicate** (a real second fill of the same entry), skip buckets
  firing on real totals/tennis/esports. `executor._deployed_usd == $4.00` (2×$2; the dup did NOT
  double-count → the CP3 interaction guarantee holds on live data). **`n_real_orders_placed == 0`.**

## 8. Latency
No live in-window action fired, so there is **no steady-state detection-latency sample this window**
(the 8 backlog latencies are age-at-detection: 0.5h–6.6h, not loop latency — do not read them as the
edge metric). What is measured/bounded: poll cadence **5.4s avg** (661.6s / 122 polls), so the poll
component ≤ ~5s; Poly's activity-API lag is ~10–60s (provenance). Steady-state detection is therefore
bounded at **~tens of seconds (≤ ~65s worst case)** — still >13× faster than the ~15-min
Kalshi-native lag. A live sample can be captured in a re-timed run near game time (offer for CP5).

## 9. placed = 0 (two ways)
- **Static:** the only `post(_V2_ORDERS_PATH)` (poly_kalshi_executor.py:261) is inside
  `if not self._dry_run:` (:258); `dry_run` defaults True (:191); the loop constructs
  `PolyKalshiExecutor(dry_run=True)` (cp4_00_shadow.py:73). Unreachable in shadow.
- **Runtime:** `shadow_out.json` `n_real_orders_placed: 0`.

## 10. Tests / attestation
42 tests green across 4 files (`-p no:pytest_ethereum`): 17 matcher + 16 executor + 9 loop. Shared
files byte-unchanged (empty `git diff`).

## 11. Not done (gate discipline) / CP5
- No config / divisions / main.py wiring; no `selected_whales` trigger wiring (CP5 operator gate).
- Guardrail **$ values are placeholders** ($2 stake / $5 per-trade / $20 daily / 2c slip); real
  numbers are the CP5 operator gate.
- No live 429 observed (unit-proven only); no live-action latency sample (offer a re-timed run).
- Open decision still pending: exit-copy handling for hold-to-resolution whales (CP2 §6) — the live
  shadow reconfirmed these whales only BUY (all 8 actions were BUY; exits are REDEEM).
