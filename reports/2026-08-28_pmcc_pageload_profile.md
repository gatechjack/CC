# PMCC division page (`/division/robinhood_pmcc`) slow-load — empirical profile

**Date:** 2026-08-28 (RTH, ~11:20–11:35 ET) · **Base:** prod-live tip `7220e32` · **Branch:** `pmcc-pageload-profile-2026-08-28`
**Mode:** READ-ONLY investigation. No code changed, no mutation of durable state, nothing placed. Timing was collected by GETting the live page on the box (localhost:8000, engine PID 676) via the sanctioned `.ps1` runner. A division GET only writes in-process caches (`pmcc_pricing._CACHE`, `pmcc_preview._STASH` — both verified side-effect-free; `propose_orders_for_pair(preview=True)` suppresses audit rows + exec-alerts), identical to the auto-refresh that already fires every 45s.

---

## TL;DR — the dominant bottleneck

A cold interactive PMCC page load is **~32 s**, and **~27 s of that is one thing**: the **inline, serial live-pricing loop** that `build_division_view` runs *before it returns the HTML* (`web/data.py:3522-3542` → `pmcc_pricing.refresh_division`). It prices all ~10 tiles one at a time, and each priced tile fans out into many **serial** Robinhood round-trips. The render **blocks** on all of it.

Everything else — the "shared" baseline (broker snapshot, positions, prices, DB, template) — is only **~5 s**, the same as a comparable division (`robinhood_ira` = 4.5 s). **The pricing loop is 100% PMCC-specific and is the whole story.** A "render-then-stream" change (drop the inline pricing; let the *already-existing* 45 s OOB refresh fill it in) takes the page from **~32 s → ~5 s** with no new infrastructure.

Latency is entirely **server-side** (`time_starttransfer` ≈ `time_total` on every GET — the browser waits on the server, not JS/DOM).

---

## Empirical wall-clock (measured, two runs)

All times are server render time (ttfb ≈ total). PMCC HTML = ~119 KB, IRA = ~40 KB (body transfer negligible on localhost).

| GET | time | what it exercises |
|---|---:|---|
| **PMCC full page, COLD** (cache >45 s) | **31.7 s** / 29.7 s / 19.1 s | baseline **+ inline pricing loop over 10 pairs** |
| **PMCC full page, WARM** (cache <45 s, steady state) | **4.7 – 5.4 s** | baseline only (pricing skipped via 45 s TTL) |
| PMCC full page, WARM (earnings 24h-cache COLD) | 12.5 – 17.7 s | baseline **+ transient 10× live earnings resolution** |
| **IRA full page** (fast-division contrast) | **4.5 s** | same broker/leg path, **no pricing loop** |
| **`/pmcc-pricing` interval endpoint, COLD** | **> 150 s** (hit my 150 s cap) | pricing loop **in isolation** over `symbols_for()` (accumulated cache, >10) |
| `/pmcc-pricing` interval endpoint, WARM-ish | 38.7 s (~15 chips) | repricing the stale accumulated subset |

**Decomposition of a cold interactive load (~32 s):**

```
  ~5 s   SHARED baseline   (snapshot + 1x get_option_positions_detail + Yahoo prices + DB + tile-status)   == same as IRA
 ~27 s   PMCC pricing loop (refresh_division: 10 pairs priced SERIALLY, ~2.7 s/pair, blocking the render)
 ─────
 ~32 s   total, initial paint
```

---

## Why the pricing loop is ~27 s — the serial fan-out (verified in source)

`refresh_division` (`web/pmcc_pricing.py:186-208`) loops symbols **serially**: `await price_and_stash(...)` then `await asyncio.sleep(0.15)` per symbol. For each **actionable** symbol, `price_and_stash → propose_orders_for_pair → detect_existing_legs` calls `broker.get_option_positions_detail()` (`brokers/robinhood.py:745`), which is:

- 1 account-wide `get_open_option_positions`, **then 2 serial calls per open leg** (`get_option_instrument_data_by_id` + `get_option_market_data_by_id`, serial `for` loop at `robinhood.py:762-784`) → **`1 + 2N`** round-trips, N = total open legs account-wide. **Not cached.**
- **Re-fetched once per priced symbol** — and `build_division_view` *already* fetched the identical account-wide detail once at the top (`data.py:3418`). So a cold load makes ≈ **`(1 + S) × (1 + 2N)`** serial position-detail round-trips.
- Plus, per symbol: 1 expiry-calendar fetch + 1 full call-chain fetch + ~4 option quotes (one is a duplicate — the buy-to-close leg is quoted in `_fresh_leg_quote` *and* again in `estimate_roll_from_quotes`).

**Single most expensive interaction:** `get_option_positions_detail` — the `2N` per-leg enrichment is serial and it is re-run per symbol.

Three serial **N+1** loops stack on the critical path:
1. **Per-symbol pricing** — `refresh_division`'s serial `for` loop (the ~27 s). *(dominant)*
2. **`get_option_positions_detail`** — `1 + 2N` serial per-leg RH calls, re-fetched per symbol (`robinhood.py:762`).
3. **`_fetch_prices`** — uses `yf.Tickers()` but then loops `.history(period="1d")` **per ticker**, each a separate serial Yahoo HTTP request (`agents/divisions/pmcc_robinhood.py:1373-1387`). Part of the ~5 s baseline.
4. **Earnings gate** — `resolve_earnings` called per pair (10×), and twice per symbol (gate + card). 24h-cached, so ~free when warm; **+8–13 s when the 24h cache is cold** (transient, recurs after cache/restart). `market_data.py` `_BROKER_EARN_CACHE` / `_EARNINGS_CACHE`.

---

## Answers to the brief

**1. Per-load work / PMCC vs fast division.** `build_division_view` is shared for all slugs (snapshot ∥ opts fetch, prices, holdings/legs/pairs, activity, equity, paper-trade). The PMCC-only block is `data.py:3510-3542`: (a) `refresh_division` live-prices every tile, then (b) per-pair `_build_pmcc_tile_status`. IRA runs the identical shared path **minus** the pricing block → 4.5 s. PMCC cold → 32 s. **The delta is entirely the pricing block.** The corp-wide `build_command_center` runs in parallel with `build_division_view` (`routes.py:531`) and is ≤4.5 s (it bounds IRA's total), so it is *not* the bottleneck.

**2. Profiled breakdown.** See table above. Dominant cost = the serial inline pricing loop (~27 s of ~32 s). Evidence: cold − warm = ~27 s; the isolated pricing endpoint alone exceeds 150 s over the accumulated cache; warm baseline ≈ IRA.

**3. Cache behavior.** `_CACHE` TTL = 45 s (`pmcc_pricing.py:20`). A reload within 45 s is a full hit → 4.7–5.4 s (pricing skipped). Any interactive navigation >45 s after the last load is **cold** → re-fetches all chains/quotes → ~32 s. So typical fresh navigations pay full price. The **stagger** (0.15 s × 10 = 1.5 s) is a small part; the serial *pricing* itself is the cost, not the stagger. Earnings lookups are 24h-cached (cheap when warm; a periodic cold-start spike otherwise).

**4. N+1 / batching.** Four serial per-item loops (listed above). The per-symbol pricing loop is the dominant N+1; the per-leg `get_option_positions_detail` (re-fetched per symbol) is the redundant one; the Yahoo `.history()` per-ticker loop and the per-pair earnings gate are the smaller ones.

**5. Blocking vs streaming.** **Blocking.** `routes.py:531` `await`s `build_division_view` (which runs `refresh_division` inline) and only then renders. The 45 s OOB streaming refresh **already exists** (`division.html:132`, `hx-trigger="every 45s"` → `/pmcc-pricing`), but the initial load **duplicates that work synchronously**. The `pricing-before-status` ordering (`data.py:3520-3522`) is exactly what forces the initial render to block on live pricing so the effective-status gate can read fresh buildability — i.e. it turned the first paint into a blocking-on-pricing render.

**6. Server vs client.** Server-side, unambiguous: `time_starttransfer` ≈ `time_total` on all GETs (e.g. cold 31.75 s / ttfb 31.75 s). No client/DOM component.

**Bonus pathology found:** the 45 s interval endpoint reprices `symbols_for(slug)` = **the entire accumulated `_CACHE`** (`pmcc_pricing.py:211` / `routes.py:1104`), which grows past the 10 visible tiles (~15 observed; unbounded as scans/digests add symbols). Cold, it took **>150 s** — it cannot finish within its own 45 s trigger, so with the page open the interval requests **stack** and pile RH load. This must be bounded to the visible tiles for any streaming design to be safe.

---

## What's PMCC-specific vs shared

- **PMCC-specific (the problem):** the inline `refresh_division` pricing loop + per-pair tile-status (`data.py:3510-3542`); `pmcc_pricing.*`; the `/pmcc-pricing` interval endpoint. ~27 s of the ~32 s.
- **Shared (fine, ~5 s):** snapshot, one `get_option_positions_detail`, Yahoo prices, DB queries, template — same code all divisions run; IRA proves it's ~4.5 s. (These *could* be optimized too — see #4/#5 below — but they are not why PMCC is slow.)

---

## Ranked tuning recommendations (no code changed here — for your review)

**1. Render-then-stream: drop the inline pricing from the initial load. — expected ~32 s → ~5 s (≈6×).** *Highest leverage, lowest risk.* In `build_division_view`, stop calling `refresh_division` synchronously; return tiles with `pricing=None` (a "pricing…" chip) and let the **already-existing** 45 s OOB endpoint fill them in. Tradeoff: on first paint the effective-status gate can't read live buildability, so an actionable tile briefly shows "pending" until the first OOB refresh lands (<1 cycle) — the exact thing the `pricing-before-status` ordering was added to avoid. That is an acceptable, arguably-correct trade for a 6× faster page. *Requires that #2 below is done first so the streamed refresh is itself fast.*

**2. Bound the `/pmcc-pricing` interval to the VISIBLE tiles; stop repricing the whole accumulated cache. — expected >150 s → a few s; stops interval stacking.** Pass the on-screen underlyings to `refresh_division` instead of `symbols_for()`, and/or evict stale `_CACHE` entries. Prerequisite for #1 to be safe (the streaming path it relies on must complete within 45 s).

**3. Fetch account-wide option positions ONCE per page and share it into the pricing path. — removes ~10 redundant `1+2N`-serial fetches per cold load.** `get_option_positions_detail` runs once in `build_division_view` (`data.py:3418`) and again inside every priced symbol's `detect_existing_legs`. Thread the already-fetched positions in (signature change to `propose_orders_for_pair`/`detect_existing_legs`). Large fraction of the pricing-loop cost, and helps the streamed refresh too.

**4. Parallelize the per-leg enrichment inside `get_option_positions_detail`. — biggest lever on the shared ~5 s baseline (helps every division).** The `2N` serial `get_option_instrument_data_by_id` + `get_option_market_data_by_id` calls (`robinhood.py:762-784`) → `asyncio.gather` with a bounded semaphore (e.g. 8). Collapses `2N` serial → `~2N/8`.

**5. Parallelize the pricing loop (bounded). — ~2–3× on the pricing/OOB refresh.** `refresh_division`'s serial `for` → bounded `gather` (concurrency ~3–4, honoring RH limits). Mostly mooted for the *initial* load by #1, but speeds the streamed OOB refresh. The 0.15 s stagger is minor — keep it as a rate-limit guard, don't bother tuning it alone.

**6. Batch the Yahoo price fetch. — shaves the baseline; helps every division.** Replace the per-ticker `.history()` loop (`pmcc_robinhood.py:1378-1384`) with a single batched `yf.download(symbols, period="1d")` (or threaded fetches).

**7. Share the earnings resolution + confirm the 24h cache. — removes the periodic +8–13 s cold-start spike.** `resolve_earnings` runs 2× per symbol (gate + card) and 10× per page; reuse `self._last_earnings_resolution` in `earnings_card_state` instead of re-calling, and consider pre-warming the 24h cache so a first-of-day load isn't 12–18 s.

**Bottom line:** #1 + #2 together solve the operator's complaint (32 s → ~5 s) using infrastructure that already exists; #3–#7 remove the underlying serial-RH redundancy that also inflates the streamed refresh and the baseline.

---

## Measurement caveats

- Robinhood API latency is variable; cold full-page loads ranged 19–32 s across samples. The *structure* (pricing loop ≫ baseline) is stable and reproduced across both runs.
- The `/pmcc-pricing` >150 s figure is a client cap (`--max-time 150`); the server continued pricing after the client aborted. Its magnitude may be partly inflated by RH throttling from my repeated test loads, but the root cause (repricing the accumulated `symbols_for()` set serially) is structural, not a test artifact. The follow-on `/pmcc-pricing` GET returned ~15 chips (>10 visible tiles), confirming cache accumulation.
- One cosmetic runner glitch: `pp.html` wasn't written because the `P_cold` curl aborted at the cap, so the in-runner chip-count printed 0 (the ~15 count comes from the `P_warm` partial size). No effect on the timing data.

## Reproduce
`cc\pmcc_profile_timing.ps1` (cold/warm + IRA contrast) and `cc\pmcc_profile_split.ps1` (baseline-vs-pricing isolation) — read-only, safe to re-run during RTH.
