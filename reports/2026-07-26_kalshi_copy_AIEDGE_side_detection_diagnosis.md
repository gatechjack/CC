# AI.EDGE side-detection failure — read-only diagnosis

**Date:** 2026-07-26 · Read-only, no fix. Feeds the operator's S2-build decision. Companion to the roster review + S2 plan.
**Verdict:** **NOT a fixable parser issue — it is a structural limitation of Kalshi copy-trading's side *inference*.** AI.EDGE is copyable only on liquid *binary* markets (World Cup games); its scalar/threshold/multivariate activity is structurally undetectable. **The roster is structurally thin, not parser-broken.** S2 fixes metrics/autopause but does **not** widen copyable supply.

---

## The mechanism (why "parser fix" is the wrong frame)

Kalshi side is **inferred**, not read. `kalshi_copy_trader._detect_side` (strategy file ~L594–634):
1. The Apify position scrape (`WhalePosition`) carries only `market_ticker`, `contracts`, `pnl`, `is_open` — **no side/outcome/avg_price field.**
2. To recover side, it fetches the market's **anonymous public trade tape** for the poll window and looks for a trade whose `count` is within **±5%** (`size_match_tolerance_pct: 5.0`) of the whale's `contracts`.
3. Confidence: **exactly 1 match → high**; **several → medium** (both get copied); **zero match → low → `kalshi_copy_entry_skipped_no_side`** (reason always `side_detection_low_confidence` — single code path).

The tape *does* carry `taker_side`; the problem is identifying **which** anonymous trade was the whale's, by size alone. That fails when: (a) the whale builds the position **incrementally** (no single trade = full size), (b) the market is **busy** (many similar-size trades), or (c) the market has **no single-ticker tape entry** (multivariate/parlay/scalar).

---

## AI.EDGE July detections — precise breakdown (61 entry-detections)

| outcome | n | driver |
|---|---|---|
| copied (`placed_live`) | 12 | liquid binary markets, unique size-match |
| **side-detection skip (`no_side`)** | **27** | **the failure under study** |
| no-fill (liquidity) | 12 | market liquidity, not copyability (excluded from copyability per your call) |
| sports-filtered (deliberate) | 9 | category filter, by design |
| other/paper leftover | 1 | — |

So of 61: **12 copied, 27 side-detection-skipped (44%)**, and 49 not-copied total (80%). *(Note: the "49" not-copied is not all side-detection — 12 are liquidity no-fills, 9 are deliberate sports skips. The side-detection-specific figure is 27.)* All 31 all-time AI.EDGE no_side skips carry the single reason `side_detection_low_confidence` (06-15 → 07-26).

## What gets skipped vs copied (the tell)

| market-type | SKIPPED (no_side) | COPIED (placed) |
|---|---|---|
| "other" (scalar/threshold/politics) | **18** | 2 |
| MVE multivariate/parlay | **10** | 3 |
| WorldCup (binary) | 2 | **4** |
| count/scalar/mention | 1 | 3 |

Skipped tickers are exactly the **non-binary / low-liquidity** ones: `KXHORMUZPEAK-26JUL26-T10`/`-T15` (scalar threshold brackets), `KXSPACEXCOUNT-26JUL-13` (count market), `KXTRUMPBIBIMEET` (politics meeting), `KXMVECROSSCATEGORY` (multivariate parlay). Copied ones are **liquid binary** World Cup games. **Post-World-Cup (mid-July), AI.EDGE shifted to scalar/threshold/multivariate markets → skip rate spiked.** This is a whale-style × detection-limitation interaction, not an AI.EDGE-specific bug (live no_side: AI.EDGE 27, pritz786 5, Maggie 1 — AI.EDGE dominates because it's most active *and* trades the hardest market types).

---

## Is it the "lengthy.starfish class"? Yes — and that class is NOT a parser bug

Two corrections to the framing:
1. **`lengthy.starfish` is a KALSHI whale, not Polymarket** (confirmed: `tests/test_kalshi_whale_intel.py::test_lengthy_starfish_copyability` seeds 1,845 `kalshi_copy_entry_skipped_no_side` vs 4 copies ≈ 0.2% copyability; `reports/2026-06-21_kalshi_copy_trading_health_results.md`). So AI.EDGE **is** the same class as lengthy.starfish — same Kalshi size-match inference.
2. **That class is structural, not a parser bug.** Polymarket has **no `no_side` failure mode at all** — its Data API returns explicit `side` (BUY/SELL) + `outcome_index` on every activity row (`polymarket_copy_trader.py` L29–31: *"Side detection is explicit … no size-match dance like Kalshi K3's trade-tape inference"*). The Kalshi failure is a **venue-specific data gap** (the profile scraper doesn't expose side), not a bug in our parsing.

So there is **no parser patch** that fixes this the way a true parser bug would. The 06-21 health report already diagnosed it: the high no-side rate means the system *declines to copy the markets it can't read* — losses come from the markets it *can* read (killed by fees), not from the skipped ones.

---

## Answer: the roster is STRUCTURALLY THIN, not fixable-parser

- Kalshi copy-trading can only reliably copy whales **while they trade liquid, binary, single-ticker markets** where a unique size-match exists.
- Both selected whales fail that regime for different reasons: **MaggieTheEagle** trades binary markets but only in **tournaments** (event-concentrated, quiet post-World-Cup); **AI.EDGE** is active year-round but drifts into **scalar/threshold/multivariate** markets that are structurally undetectable.
- **This is upstream of S2.** S2 fixes the *dashboard + autopause instrumentation*; it does **not** add a single copyable trade. Even with perfect metrics, copyable supply stays thin until the *roster* or the *detection data source* changes.

### Options (report only — operator decides; none is "just patch the parser")
1. **Widen `size_match_tolerance_pct`** (e.g. 5%→10%): marginal — only helps the "no single match" case, raises wrong-side risk, does nothing for incremental-build or no-tape-entry cases. Low value.
2. **Explicitly filter non-binary markets** (KXMVE/scalar/threshold) *before* `_detect_side`: cosmetic — cuts skip-noise, adds **zero** copies. Cleans the audit only.
3. **Shift roster-selection toward year-round *binary-market* whales** (favor whales whose activity is dominated by liquid single-ticker binaries): the only option that actually widens copyable supply — a selection-criteria change, not a parser fix.
4. **Better side data source for Kalshi** (an explicit-side feed like Polymarket has): large lift; may not exist for Kalshi profile scraping.

**Bottom line for the S2 decision:** S2 is still worth doing (trustworthy metrics + functional autopause are prerequisites for *any* roster management). But it will reveal, not cure, a structurally thin copyable-supply problem. The roster question — whether to re-select toward binary-market whales — is the higher-leverage lever, and it's independent of S2.

---

**Caveats:** market-type buckets are ticker-prefix heuristics; "other" = scalar/threshold/politics by inspection of the actual skipped tickers. Read-only; no code/config/roster change; no edge/prospect memory written.
