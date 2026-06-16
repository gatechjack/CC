# Entry-latency investigation — alert→entry delay (502/ingress + PA-redeem)

**Date:** 2026-06-16 · **Mode:** read-only investigation (agent SSH read-only, disclosure 82fda13). No code/fix/deploy/prod-write; no signed/public-API calls. Evidence = prod journald (trading-corp + caddy), Caddy config, strategies.yaml, webhooks.py / main.py source reads, trading_corp.db (read-only).

---

## TL;DR — where the 6 minutes go

**The delay is an internal, recurring TOTAL EVENT-LOOP FREEZE of the trading engine (6–13.6 min, ~6×/day), NOT TradingView lag and NOT — for the actual bad trade — an ingress 502.**

The webhook receiver (FastAPI/uvicorn) runs as **one asyncio task on the same single event loop as the entire trading engine** (`main.py:2186 — asyncio.create_task(server.serve())`). When any synchronous/slow upstream call blocks that loop, *everything* freezes — including the webhook receiver. Two manifestations, same root:

1. **Loop moderately blocked → request queued, processed late, ACTED ON STALE.** uvicorn accepted the TCP connection but the handler coroutine can't run for minutes; when the loop thaws it processes the now-stale alert and **places the order**. → the 00:38 trade.
2. **Loop severely blocked → accept-backlog full → connection refused → Caddy 502 → TV retries → alert LOST.** → the operator's 05:06 "spoon_bull" (4× 502, never delivered).

TradingView is **prompt** (delivered within ~0.4s of the bar). The 502s are real and are the *severe form of the same freeze*, not a TV problem.

**Confidence: HIGH** on the decomposition (direct log evidence: freeze boundaries via a journal-silence test, Caddy `status:0`, `connection refused`). **MEDIUM** on which upstream call triggers the freeze (temporal correlation, 2 clean examples). **Sample of traded-and-stopped-out late entries is N=1** (the 00:38 trade) because lord_otter/market_cypher coinbase consumers are disabled and bitunix trades are sparse — but the freeze/latency *phenomenon* is well-sampled (6 freezes today, 275 webhook gap measurements over 2 days).

---

## Architecture (ingress path)

```
TradingView ──HTTPS──> Caddy :443 (trading.jacksumner.com)
                         │  @public bypass: /webhook/tradingview/{lord-otter,market-cypher}
                         │  (NO Authelia on webhook paths — auth adds 0 latency)
                         └─ reverse_proxy localhost:8000
                                          │
                              uvicorn :8000  (FastAPI)  ── asyncio.create_task(server.serve())
                                          │                on the SINGLE engine event loop
                              handler: validate → audit webhook_received → background_tasks.add_task → return 200
                              bg task: snapshot → on_alert → (PA-redeem) → risk gate → place
```
- Ingress is **Caddy** (not nginx). nginx absent. 502 Bad Gateway = Caddy↔uvicorn.
- Handler is **fast-ack by design** (`webhooks.py`): validates then dispatches heavy work to a FastAPI BackgroundTask. `webhook_received` audit is emitted in the **synchronous handler phase** (webhooks.py:257), *before* `add_task` — so its timestamp = when the loop actually ran the handler.
- **The fast-ack/queue design is already implemented but DEFEATED**: a full-loop freeze starves the uvicorn task itself, so it can't ack *or* even accept.

---

## Part A — latency decomposition

### A.0 Reconciling the operator's "01:06 spoon_bull SHORT that stopped out"

The cited trade does **not exist as described** in engine records:
- **No `spoon_bull` alert with payload `05:00:00Z`.** Jun-16 spoon signals: 04:19 spoon_bear, 09:06 spoon_bear, 10:05 spoon_bull (bar 10:00:01Z), 11:48 spoon_bull. All logged `lord-otter ignored: strategy disabled in config`.
- **`lord_otter` is `enabled: false`** (strategies.yaml:488); `market_cypher` also `false` (679). These are the coinbase_spot consumers. spoon/cvd signals therefore cannot place a trade.
- **No bitunix order was placed post-deploy** (deploy restart 04:55:55Z; bitunix_futures filled count for all of Jun-16 = 1, the pre-deploy 00:38 trade).
- **What actually happened at ~05:06 (01:06 EDT):** Caddy shows **four `/webhook/tradingview/lord-otter` POSTs at 05:06:02–05:06:17, all 502** (`dial tcp 127.0.0.1:8000: connect: connection refused`). This is the operator's "3m_Otter spoon_bull." TV's log showed "delivery failed 502"; our ingress refused all four attempts during a startup-priming freeze. **It never reached the app and never traded.** (At 05:00:03–31, four `market-cypher` POSTs likewise 502'd.)

The real, decomposable late-entry-that-stopped-out is the **00:38 `mc_a_redx` (market_cypher) trade** — used below as the analog.

### A.1 The 00:38 trade — full timeline

| Time (UTC) | Event | Evidence |
|---|---|---|
| 00:27:02.000 | 3m bar OPEN (TV `{{time}}`) | payload `time` = bar OPEN (webhooks.py:59) |
| ≈00:27:02.4 | **TV POST hits Caddy** (req start = log_ts − duration) | Caddy access: start≈02.4 |
| 00:27:05.2 | Caddy logs `status:0, duration:2.82s` — **TV gave up waiting for our response** | `http.log.access status:0` |
| **00:25:19 → 00:38:34** | **ENGINE FROZEN 795s (13.25 min)** — zero trading-corp journal lines | journal-silence test: 0 lines in window |
| 00:38:34.4 | loop thaws → handler runs → `market_cypher/webhook_received` | audit row |
| 00:38:36.4 | background task → `bitunix_futures/live_order_placed` (SELL) | audit row |
| 00:38:37.4 | filled @ 66345.8 | `data_exec/filled` |
| (later) | stopped out → result=loss, −0.0569 USDT (`auto_booked_from_stop_level`) | paper_trade_record |

**Segment attribution:**
- TV evaluate+send+network: **~3s** (bar-open → Caddy receipt). PROMPT.
- **Event-loop freeze: ~11.5 min (00:27 → 00:38:34) — owns ~99% of the delay.**
- Internal processing (handler+gate+place): **~2s**.
- **PA-redeem: 0** (placed 2s after webhook_received — no per-bar re-eval delay on this trade).
- Caddy/Authelia: 0 (webhook bypasses Authelia; Caddy adds <1ms when the upstream is up).

The 6–11 minutes are **entirely the loop freeze.**

### A.2 The 502/ingress hypothesis — CONFIRMED (as the severe form of the freeze)

Every webhook 502 today is `dial tcp 127.0.0.1:8000: connect: connection refused`, returned by Caddy in ~0.0004s (instant). Connection-refused = uvicorn's accept-backlog was saturated → the event loop was so blocked it couldn't `accept()`. TV retries ~4× over 15–30s; if all fail, the alert is **lost** (05:00 and 05:06 clusters). So the 502s are not a separate "proxy capacity" problem — they're the loop freeze in its acute phase.

### A.3 The TradingView-side hypothesis — REFUTED

`status:0` with the request *starting* at ≈bar-open proves **TV sent within ~0.4s of the bar and waited for us**; it was *our* non-response that aborted the request. The variance is the tell: identical-type alerts arrive in ~1s when the loop is free (11:48 spoon_bull: bar 11:48:01 → received 11:48:02) and 5–13 min when it's frozen (10:05 spoon_bull processed exactly at the end of an 818s freeze). TV does not randomly vary by 11 min for the same signal — the loop does.

### A.4 PA-redeem (the known backlog item) layered in

PA-redeem latency is a **separate, downstream** segment: it occurs *inside* `agent.on_alert` (the background task), **after** `webhook_received`. The gap measured here (bar → `webhook_received`) is purely **delivery + loop-freeze**, upstream of redeem. For the 00:38 trade, redeem added 0. So total entry latency = **delivery (~3s) + loop-freeze (0–13 min) + redeem (0–N bars) + processing (~2s)**, and this trade was freeze-dominated with zero redeem.
> ⚠ Cross-finding: the prior entry-timing analysis attributed multi-bar lateness to "PA-redeem." Part of that may actually be **loop-freeze** (bar → process), which it could not see without the Caddy/journal-silence decomposition. Recommend re-checking that analysis to separate freeze-delay from redeem-delay.

---

## Part B — severity + threat

### B.5 Is it getting worse?

**Bursty / inconclusive trend; persistently severe baseline.**
- All-path 502/day (Caddy): 06-12=**0**, 06-13=**123**, 06-14=**117**, 06-15=**8**, 06-16=**159** (half-day). Webhook-only 502/day: 0 / 36 / 8 / 8 / 12. Highly variable, **correlated with restarts** (today's 12 webhook-502s all in 04:56–05:06, the post-restart startup-priming window; zero after 05:06).
- Webhook bar→process gap: **06-15** n=177, <30s=148 (84%), ≥3min=23 (13%), >10min=5, mean 64s, max 685s. **06-16** n=98, <30s=83 (85%), ≥3min=13 (13%), >10min=5, mean 76s, max 747s. Mild upward drift (mean 64→76s; >10min 3%→5%), small sample.
- **Freezes today (since 05:10): 6 events of 6.4–13.6 min** (thaw at 06:23, 07:30, 07:37, 08:51, 10:05, 11:19) ≈ **~16% of wall-clock frozen.** The operator's "getting worse" likely reflects today's elevated freeze frequency rather than a proven monotonic trend.

**Broader threat:** a freeze stalls the *entire* engine, not just webhooks — the bitunix 60s position reconciler, all scanners, and risk/exit management are also dead for 6–13 min. A stop-management action or divergence detection could be delayed by a freeze, independent of entries.

### B.6 Receiver capacity — the fixable root

- **Single process, single event loop.** uvicorn is `asyncio.create_task(server.serve())` (main.py:2185–2186) co-resident with every agent, scanner, broker poll, and DB op. Workers=1 implicitly (it's a task, not a worker pool).
- **The queue/async accept-then-process pattern is already there** (fast-ack + BackgroundTask) — but it cannot help when the loop *itself* is frozen by a blocking call.
- **Freeze trigger = synchronous/slow external HTTP on the loop** (high confidence it's I/O; medium on which call):
  - Freeze thawing 06:23:12 surfaced `BitUnix funding_rate fetch failed` + `LiveBarCache refresh failed: ConnectTimeout` → a BitUnix bar-cache/funding HTTP call hung ~13 min.
  - Freeze ending 10:05:32 began right after a `polymarket_scan_cycle` (Polymarket data-API).
  - Consistent with the prior session's BitUnix `get_pending_positions` ConnectTimeout / "Server disconnected" warnings. A blocking client (or async without aggressive timeouts) against a blackholed/slow upstream freezes the loop for the full connect-retry duration.
- **Existing freshness gate is too loose:** `_REPLAY_WINDOW_SEC = 1200` (20 min) is the *only* bar-age check (`abs(now − bar_time) > 1200 → reject`, webhooks.py:230). The 00:38 alert's skew was 692s < 1200 → it passed and traded.

---

## Part B.7 — Fix options (scoped, NOT built)

**C (quick mitigation — recommended first; would have PREVENTED the 00:38 bad trade): staleness-reject gate.**
Add a tight, bar-interval-aware freshness check distinct from replay protection: reject entry if `now_at_processing − bar_open_time > bar_interval + margin` (e.g. >240–300s for a 3m bar; `interval` is in the payload). Cheapest — the skew is already computed at webhooks.py:230; this is a threshold change + interval-awareness. Does **not** fix freezes, but stops the engine from entering on a 6–13-min-stale signal after a thaw. *Risk:* slightly more rejected entries; acceptable for a scalper where a stale fill is −EV.

**A (root — addresses freezes, benefits whole engine): de-block the event loop.**
Make all external HTTP truly async with aggressive connect/read timeouts (e.g. 3–5s), and/or run any unavoidable blocking call in `asyncio.to_thread`/an executor so an upstream hang can't freeze the loop. Targets: BitUnix bar-cache/funding refresh, BitUnix snapshot/position polls, Polymarket data-API. *Follow-up needed:* pinpoint the exact blocking call(s) via code audit/profiling (medium-confidence list above).

**B (isolation — defense-in-depth): separate the webhook receiver from the engine loop.**
Run uvicorn in its own process (or a dedicated thread with its own loop), writing accepted alerts to a durable queue (DB/IPC) that the engine drains. Then an engine freeze neither refuses (502→lost) nor silently delays receipt: alerts are **captured + timestamped on arrival**, and the staleness gate (C) can act on the true receipt time. Heavier change.

**Recommended sequencing:** C now (stops bad fills immediately, tiny change) → A (kills the freezes, helps everything) → B (durability) if freezes persist.

---

## Evidence appendix (key raw findings)
- Caddy 05:06:02–17: 4× `lord-otter` → 502 `connection refused` (the operator's spoon_bull, lost).
- Caddy 00:27:05: `market-cypher status:0 dur:2.82s` (TV gave up; req started ≈bar-open).
- journal-silence test: 0 trading-corp lines 00:27:08–00:38:30 (freeze 00:25:19→00:38:34, 795s).
- 6 freezes ≥6min since 05:10 today; 06:23 thaw = BitUnix ConnectTimeout; 10:05 freeze post polymarket_scan_cycle.
- gap dist 06-15/06-16 as in B.5; `_REPLAY_WINDOW_SEC=1200`; uvicorn at main.py:2186 on the shared loop.
- lord_otter/market_cypher `enabled:false`; only bitunix_futures live; bitunix_futures filled Jun-16 = 1 (pre-deploy 00:38).

*No changes proposed beyond the above scoped options. No code written, no prod state touched.*
