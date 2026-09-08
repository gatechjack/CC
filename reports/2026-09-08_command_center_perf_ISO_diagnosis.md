# Command-Center Render Latency — Component Isolation + Fix (2026-09-08, PM)

**Mode:** READ-ONLY diagnosis + LOCAL fix build. No deploy/restart/arm/push, no live-DB write.
**Engine:** MainPID=232440 / PYPID=232453, `python -X utf8 -m trading_corp --live --brokers bitunix`,
up since 2026-09-07 15:56Z, nproc=2. yfinance 1.3.0, curl_cffi 0.15.0.
**Picks up:** `2026-09-08_command_center_perf_regression_diagnostic.md` (prior agent, ~80%).

## TL;DR
The slow page is `build_command_center` (served at `/`, `/partials/stat-cards`,
`/partials/market-ribbon`, and — the trap — every `/division/{slug}`). The cost lives in
**`_hydrate_division_metrics`** (data.py:912): it calls a **live `broker.snapshot()` for every
enabled division on every render, concurrently, with NO timeout**, so the render is hostage to
the slowest broker's live latency. Measured **6–10s this afternoon; ~28s at the AM peak**.
The gather@774 (yfinance ribbon/VIX/regime + DB) is **≤2.3s cold, ~0.2–1.5s warm** — not the cost.

The prior agent's two headline conclusions are **refuted by measurement**:
- **NOT yfinance/Yahoo rate-limiting.** Fresh-process `yf.download(SPY,1y,1d)` = 0.10–0.37s and
  `Ticker.history` = 0.03–0.11s from the box IP; VIX/quote/intraday all <0.16s (iso5 = 2.27s for
  ALL yfinance legs cold, sequential). The live render's Yahoo socket completes in <2s. yfinance
  is TTL-cached (quotes/intraday 60s, VIX 5min) so it is ~0 on repeat renders anyway.
- **NOT asyncio thread-pool / CPU starvation.** During renders the engine's thread census is
  **R=0–1, D=0, S=12–13** (13 threads, nearly all sleeping) — pure I/O wait, no CPU queue.

## Decisive measurements (all VERIFIED — command + output in cc/perf_iso{1..5}_ro.*)
| # | Measurement | Result | Kills / confirms |
|---|---|---|---|
| B1 | render #1 then #2 within 1s | 27.7s then 15.1s | culprit is UNCACHED (not the 60s-cached ribbon/VIX) |
| iso2-1 | 6 tight back-to-back renders | 10.1/6.5/8.7/7.8/6.4/9.1s | NOT a fixed 28s timeout — a variable elevated floor |
| iso2-2 | **trade-flow ‖ stat-cards** (concurrent) | trade-flow **0.02s** while stat-cards **7.1s** | event loop NOT saturated; slow work **yields the loop** (async awaits, not a sync DB block) |
| iso2-3 | polymarket data-api `/positions` during a render | **0.07–0.18s** | data-api NOT the bottleneck |
| iso2-4 | poller intensity now | **~0–1/s**, renders still 6–10s | render slowness NOT poller-load-driven |
| iso1-C | thread census during renders | **R=0–1, D=0, S=12–13** | NOT CPU/thread-pool starvation; NOT disk |
| iso1-E | fresh yf.download(1y)×3 / Ticker.history×2 | 0.10/0.10/0.14 · 0.11/0.03s | yfinance/Yahoo NOT rate-limiting the box IP |
| iso3-3 | per-broker public-endpoint latency during a render | coinbase 0.11 · kalshi 0.10/0.13 · bitunix 0.30 · polygon-rpc 0.10s | NO broker host is network-slow |
| iso5 | gather@774 yfinance legs (cold, real fns) | **2.27s total sequential** (VIX 1.5 cold; regime 0.16; rest <0.11) | gather@774 is small; warm even smaller |
| iso4 | `/division/{slug}` per broker (×16) | ALL 6–9s incl. paper/stub brokers | **THE TRAP:** `/division` gathers `build_command_center` too (routes.py:540) — re-measures the same fn; does NOT isolate brokers |

## The chain of subtraction (component named with numbers)
`build_command_center` = gather@774 (parallel) → `_hydrate_division_metrics`@794 → donchian@800 → pm_overview@810.
- gather@774: yfinance ≤2.3s cold / ~0.2–1.5s warm (VIX+ribbon TTL-cached on repeat; regime `yf.download` 0.16s; DB fast). Runs as ONE concurrent gather ⇒ wall ≤ max leg.
- donchian@800 + pm_overview@810: **synchronous** DB reads — would block the event loop; trade-flow stayed 0.02s concurrent ⇒ they are fast (not the cause).
- ⇒ residual **≈ render − ~1s ≈ 5–9s = `_hydrate_division_metrics`**, the concurrent live `broker.snapshot()` fan-out over ~14 enabled divisions (`asyncio.gather`, so wall = the **slowest single snapshot**).

## What we could NOT isolate (honest gap)
**Which specific broker** owns the slow snapshot. Two blockers: (a) the only per-division endpoint
(`/division/{slug}`) re-runs `build_command_center` (the trap above), so it can't isolate one broker
read-only; (b) making authenticated side-process broker calls (Kalshi is live money, 24 armed subs;
Robinhood shares a session file) was declined to avoid disturbing live sessions. What IS known:
network RTT to every broker host is <0.3s, so the slow snapshot is **server-side latency / many
sequential round-trips inside an authenticated `snapshot()`**, not connectivity. By code, the highest
round-trip-count candidates are **Coinbase spot** (`ccxt.fetch_balance` account pagination + per-asset
valuation) and **Kalshi** (`get_balance`+positions across 24 subs); Robinhood is ~3 calls; Fidelity fell
back to a bare in-memory `PaperBroker` (data_exec.py:156, VERIFIED) so it is fast; Polymarket data-api
is fast (iso2-3). This is *remaining*, not concluded.

## Fix (demonstrated-cause-directed, broker-agnostic, display-layer only)
Cause demonstrated: **the render blocks on live broker snapshots with no timeout.** Fix bounds each
snapshot and serves last-known-good on timeout — contained to `trading_corp/web/data.py`
(NOT a grafted-shared order-path file; no migration):
- Wrap each `broker.snapshot()` in `asyncio.wait_for(..., timeout=_SNAPSHOT_TIMEOUT_SEC)` (default 3.0s,
  env-overridable).
- Keep a module-level last-known-good `_SNAPSHOT_CACHE[slug]`; on timeout/failure serve the cached
  snapshot (equity persists) instead of flapping to `not_wired`/$0; `None` only if never seen.
- Result: fast brokers stay fresh; a slow broker degrades to last-known and the render is bounded to
  ~timeout (worst case ~3s) instead of 5–28s. Also protects against ANY future slow broker.

Before/after is proven locally with a mock slow broker (see the committed test).

## Why it "regressed this weekend" (unchanged verdict, refined mechanism)
Render-path code is old/unchanged. The AM ~28s vs PM ~7s is the **same** problem at different broker
latencies; it is broker-server-latency-driven, not a code edit. (Prior "concurrency load" narrative is
partially refuted: poller-idle renders are still 6–10s.)

## Fix result — before/after (VERIFIED, local)
`tests/test_hydrate_snapshot_timeout.py` (webtest venv, py3.12): with one 3.0s-slow broker,
`_hydrate_division_metrics` went **3.00s (before, no timeout) -> 0.52s (after, timeout=0.5s), 5.8x**;
a post-cache timeout serves last-known equity (777.0) in 0.50s; `data_exec=None` still marks not_wired.
Existing dashboard/data-path tests pass (50/50: test_dashboard_price_fetch, test_division_live_badge,
test_data_exec_stale_snapshot, test_approvals_routes). In the live render (default timeout 3.0s), a
5-9s broker snapshot is cut to ~3s -> render bounded to ~3s worst case vs the observed 6-28s.

## Deploy ledger (RESERVED FOR JACK — push + deploy + restart)
Branch `perf-command-center-2026-09-08` off prod-live `a24b8bf` (verified tip); 3 commits, clean FF:
- `6976eb4` docs (this report)
- `49a4af5` fix (data.py: wait_for + last-known cache)
- `741f293` style (ASCII-only comments -> pure-ASCII deploy graft)

Only code file changed: `trading_corp/web/data.py` (+28/-2, pure ASCII). NOT a grafted-shared
order-path file. No migration. Env knob: `TC_DASH_SNAPSHOT_TIMEOUT_SEC` (default 3.0).

1. **Push (FF prod-live):**  `git push origin perf-command-center-2026-09-08:prod-live`
   (linear FF from a24b8bf; the prod-live ruleset allows FF, blocks force/delete.)
2. **Deploy (box graft):** box `web/data.py` should equal a24b8bf blob `06642ac` (prod-live==box per
   2026-09-07 reconcile) -> the 3 hunks in this branch's data.py diff apply clean; verify the pre-graft
   sha, apply, verify post-graft sha == branch blob. build_command_center runs INSIDE the main engine
   process, so the change is inert until restart.
3. **Restart:** `C:\Users\AA Incorporado\Desktop\restart_tc.ps1` (systemctl restart trading-corp) —
   full engine restart (all divisions briefly down + boot reconcile); Jack's timing (off-hours ideal).
4. **Post-check (read-only):** `curl :8000/partials/stat-cards` should be ~<=3s (was 6-28s); tiles
   render; divisions previously online do not flap to not_wired.

## Recommended follow-up (NOT built — needs broker ID)
This fix bounds the SYMPTOM (render latency), robustly and broker-agnostically. The ROOT — one
authenticated broker `snapshot()` taking 5-9s server-side (top candidates: Coinbase spot
`ccxt.fetch_balance` account pagination; Kalshi balance+positions across 24 subs) — is not fixed at
source. Pinning it needs live-process instrumentation (a 2-line per-broker timing log deploy) or a
credentialed read-only harness; then that broker's snapshot can be sped up / cached at source.

## Read-only guarantee
curl timings, `ss`/`/proc`/journalctl reads, `mode=ro` sqlite, ephemeral box-venv yfinance timing,
read-only source inspection. No box file changed, no service cycled, no DB write, no py-spy.
Runners: `cc/perf_iso{1..5}_ro.{ps1,sh}`.
