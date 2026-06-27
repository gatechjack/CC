# Retire replay's BitUnix API pulls → read from local bitunix_bar_history — SCOPE (read-only, 2026-06-27)

Required durable fix: the `paper_trade_replay` classifier re-pulls BitUnix klines **every 15 min +
on every boot**, per pending paper trade. This repetitive automated traffic is what built the
Cloudflare bot reputation; a clean egress IP will get **re-flagged** unless this loop stops hitting the
BitUnix API. Goal: serve the same bars from the local `bitunix_bar_history` table (which we're
backfilling) instead. **Read-only scope; gated like any prod change; not deployed.**

## What it fetches today
- Entry points (all accept an injectable `ohlcv_fetcher`): `start_replay_loop(interval_sec=900)`
  (main.py:1598) + the boot catch-up `replay_pending_paper_trades_async` (main.py:1557).
- Per pending trade (paper_trade_replay.py:927-936):
  `bars = await fetcher(row.symbol, "1m", since_ts_ms=entry, bars_needed=max_hold//60)`
  → walks the trade's lifecycle bar-by-bar to classify win / loss / expired (`_classify` /
  `_classify_v2_multi_leg`, which read each bar's high/low to detect TP/SL touches).
- Default routing (`_default_router_fetcher`, :1244): **bitunix symbols → `_bitunix_kline_fetcher`**
  (the paginated BitUnix API call we want to retire); everything else → Coinbase via ccxt.

## ★ The blocker: timeframe mismatch
The classifier requests **`"1m"`** bars. The local table holds **3m / 15m / 1h / 4h / 1d — there is NO
1m** (confirmed: distinct timeframes = 15m,1d,1h,3m,4h). So "just read the local table" returns nothing
for 1m. The repoint is **not drop-in** — the timeframe must change.

### Options
- **(A) RECOMMENDED — switch BitUnix replay to 3m from the local table.** We have 3m (backfilled to
  2025-11-01 + live capture forward). Removes **all** BitUnix replay API calls. Cost: **intrabar
  precision** — within a single 3m candle the classifier can't order TP-vs-SL if *both* are touched in
  the same bar (1m would disambiguate). For normal R-multiples this is rare; the classifier needs a
  documented conservative tie-break (e.g. assume SL-first / mark ambiguous). Win/loss verdicts are
  otherwise identical (3m high/low still captures whether a level was reached).
- **(B) capture/backfill 1m too** — preserves precision but **counterproductive**: a live 1m cache
  *increases* the ongoing BitUnix API footprint (the very thing that flagged us), and 1m history is 3×
  the volume. Reject.
- **(C) hybrid** — local 3m + API only for the recent 1m tail. Still leaves 1m API calls on a cadence.
  Partial; not worth the complexity. Reject.

## How to serve the same from local (change surface for the build — option A)
1. **New fetcher** in paper_trade_replay.py, e.g. `_make_local_first_fetcher(db_url)` returning an async
   `(symbol, timeframe, since_ms, limit)` fn:
   - BitUnix symbol → query the local table:
     `SELECT ts_ms,open,high,low,close,volume FROM bitunix_bar_history
      WHERE symbol=? AND timeframe='3m' AND ts_ms>=? ORDER BY ts_ms LIMIT ?`
     (symbol via `_to_bitunix_symbol`; rows returned as `[ts_ms,o,h,l,c,v]` — exact fetcher contract).
   - non-BitUnix → `_coinbase_ccxt_fetcher` (unchanged; Coinbase keeps 1m via ccxt).
2. **Venue-aware timeframe/count** at the call site (:935-936): BitUnix → `"3m"`, `bars_needed =
   max(1, max_hold//180)`; Coinbase → `"1m"`, `//60` (unchanged). (Today both are hardcoded 1m//60.)
3. **Inject** the local-first fetcher at both main.py call sites (`ohlcv_fetcher=`): the loop (:1598)
   and the boot catch-up (:1557). The injection hook already exists — no routing rewrite needed.
4. **Tie-break** for same-3m-bar TP+SL ambiguity, documented + tested.

## Coverage / correctness checks (for the build, not done here)
- The local table (backfilled to 2025-11-01 + live forward) covers all plausible pending paper trades.
  The recent tail is archived within ~60s of bar close, so by the 15-min replay tick the bars exist;
  a just-closed trade simply classifies on a later tick (vs today's per-tick API re-fetch) — acceptable.
- BitUnix paper trades currently drive this (bitunix_futures = paper). SFP is live (not replayed here).
- Verify `_classify` / `_classify_v2_multi_leg` behave on 3m bars (esp. v2 multi-leg TP1/TP2/SL ordering)
  — this is where the precision caveat lives; needs a test comparing 1m-vs-3m verdicts on real trades.

## Gate / deploy posture
- This is a **prod code change** to `paper_trade_replay.py` (+ small main.py wiring). Deploy gated like
  any: targeted diff, full test suite == baseline, **drift-gate vs PROD md5** for paper_trade_replay.py
  + main.py before staging, runbook + operator-run restart. **Not deployed; scope only.**
- Sequencing vs the egress swap: the swap restores connectivity *now*; this repoint is what keeps the
  new IP clean. Land the backfill (so the table is complete) → then this repoint → then the API sees
  only the live poll. Order: egress swap → backfill → replay repoint.

## Net
After (A): the only BitUnix API traffic is `live_bar_cache._refresh_bitunix` (live poll) + optional
small outage gap-fills. The every-15-min + every-boot replay re-fetch — the reputation-builder — is gone.
