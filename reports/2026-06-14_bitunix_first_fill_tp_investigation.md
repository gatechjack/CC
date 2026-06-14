# Bitunix first live fill — "missing TP1" investigation (READ-ONLY)

- **When:** 2026-06-14 ~18:35–18:50 UTC (fill occurred 18:24:08 UTC).
- **Method:** Agent read-only SSH per `82fda13` (`sqlite3 -readonly`, `journalctl`, `systemctl`, file reads) + local source review. **No place/cancel/modify on the live position. No prod writes.**
- **Prod:** `trading-corp.service`, PID 2637434 (go-live boot 2026-06-13 15:36; etimes ~27h, NRestarts 0). DB `/home/azureuser/trading_corp/data/trading_corp.db`.

## VERDICT (plain)

1. **Trade FILLED, position OPEN, catastrophic STOP resting server-side on the exchange.** ✓
2. **TP1/TP2/TP3 are NOT — and were NEVER — resting orders on the exchange. They are virtual, bot-managed levels BY DESIGN.** The operator seeing "a stop but no TP1" is the architecture working as intended, **not** a failed, rejected, or lost take-profit.
3. **No error on the order / fill / stop path.** One *new, non-load-bearing* yfinance log-spam anomaly was triggered by the open position (cosmetic; ruled out of every protection path).
4. **B1 server-side stop: first real-fill validation PASSED** — the entry + `slPrice` landed atomically; the stop is live on the exchange.

**Open-risk framing (design, operator-accepted — not a defect):** the position's only *exchange-resting* protection is the catastrophic stop. Profit-taking and the SL-ratchet depend on the bot's replay loop (cadence ~15 min) being alive. The server-side stop bounds catastrophic loss instantly; TP-taking has up to ~15-min granularity.

## The fill
- internal `order_id` `6741f62f-d950-4356-8deb-578f603f8db0`; venue_order_id `2066225186781114368`; clientId `tc-6741f62f-...`.
- **SHORT (sell) BTC/USDT.P**, qty **0.000485496950614426 BTC** (~$30.9 notional @ 63678; 25× leverage; recorded `max_dollar_risk` ≈ **$0.122**). Tiny first live trade.
- planned entry 63679.4 → **fill_price 63678.1**; accepted+filled **18:24:08 UTC**.
- tier STANDARD, signal `mc_a_red_diamond`, entry fee $0.0051.
- Audit chain clean: `pa_validation_decision`(pass) → `htf_gate_decision` → `trade_plan_decision` → `live_order_placed`(intent) → `BitUnix place_order accepted` → `data_exec/filled`.

## The position (open)
- `paper_trade_record` `result = NULL` (OPEN), `execution_mode = live`, `broker_order_id` set.
- `stop_price` / `current_sl` = **63805.3397** (the SL attached to the entry as `slPrice`).
- `tp_plan` v2: **tp1 63564.77708** (25%, → breakeven) · **tp2 63553.4603** (50%, → tp1) · **tp3 63364.55075** (25%, trail_atr).
- **`filled_legs: []`** — no TP leg reached. Correct: price ~63710 is *above* the short entry (slightly underwater), between entry and SL, far below none of the TPs (TPs are lower).
- replay tick 18:33:52: `scanned 1, still_open 1, errors 0` — actively, healthily tracked.

## What orders are actually on the exchange
- **STOP — YES.** `bitunix.py:_build_order_body` attaches `slPrice=63805.34`, `slStopType=MARK_PRICE`, `slOrderType=MARKET` to the SAME open call (B1). Born atomically with the position; this is the stop the operator sees. The order filled with no param-error → the venue accepted the attached stop.
- **TP1/TP2/TP3 — NO, by design.** The code never sends `tpPrice`/`tpStopType`/`tpOrderType` (these strings appear **nowhere** in the codebase). TPs are virtual levels the bot monitors via the replay loop (`paper_trade_replay.py`); when a bar-walk detects price reaching a leg, the bot places a **reduce-only MARKET close at that moment** (`_execute_live_exits`). No resting TP order is ever placed.

→ "stop visible, no TP1" = **expected**, not a missing/failed leg.

## Errors
- **Order / fill / stop path: NONE.** No reject, no `slPrice` param-error, no stuck/halt, no 10006 on the order path since the fill.
- **One NEW cosmetic anomaly:** yfinance logs `$BTCUSDT: possibly delisted; no price data found (period=1d)` + `HTTP 404 Quote not found for symbol: BTCUSDT` every ~10s, **starting 18:24:16** (8s after the fill; **ZERO occurrences before** — count = 0 boot→fill).
  - **Cause:** a per-open-position price poll in the display/monitoring layer feeds the perp symbol *mistranslated* to `BTCUSDT` (yfinance expects `BTC-USD`).
  - **Impact: non-load-bearing.** Ruled out of every protection path: the replay loop (TP/SL detection) uses **BitUnix native klines** (`_bitunix_kline_fetcher`, no-auth; replay `errors:0`); the catastrophic stop is **server-side**; the snapshot-staleness gate uses the **BitUnix broker snapshot**, not yfinance. The boot-time data_exec yfinance feed never started (no "polling started" at boot). The yfinance value just returns empty → cosmetic ERROR log-spam.
  - **Exact caller not pinned to a line** (yfinance's own logger emits the ERROR regardless of the wrapper). Candidates: dashboard auto-refresh / a position-PnL monitor. **P3:** map crypto-perp symbols to `BTC-USD` (or skip yfinance for perps) in the display price path.

## B1 (server-side stop) — first real-fill validation
- **EXERCISED FOR REAL and PASSED.** Entry + `slPrice` landed atomically (order filled, no param-error) → catastrophic stop resting server-side at 63805.34 on MARK_PRICE. First real-fill confirmation of the previously-UNVALIDATED B1 path.
- **The TP path does NOT share B1's mechanism** — TPs were never server-side, so there is no "TP placement failed where the stop succeeded." Two different mechanisms: stop = server-side `slPrice` (atomic with entry); TP = bot-side reactive reduce-only close.

## Recommended (NO action taken — operator decides)
- **Nothing required on the live position.** It has its catastrophic stop; TPs fire bot-side when levels are hit (price must fall ~114 pts to tp1).
- If the operator wants *exchange-resting* TPs (so TPs survive a bot outage), that is a **design change** (attach `tpPrice`, or pre-place a reduce-only TP ladder) — a separate decision, not an incident fix.
- **P3:** fix the yfinance crypto-perp symbol mapping to stop the ~10s ERROR log-spam.
