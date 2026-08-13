# kalshi_resolver — settlements silently not booking (finalized-not-settled gap)

**Date:** 2026-08-01 · **Mode:** READ-ONLY diagnosis (no fix, no config/deploy). Triggered by 10 post-epoch kalshi_llm positions past expiry (07-14/16/23) still showing OPEN.
**Verdict:** **RESOLVER BUG, not a Kalshi-side void.** The markets ARE resolved on Kalshi; the resolver isn't booking them. Same *class* as the Maggie-latch and the kalshi_arbitrage backlog: settlements silently not booking → **forward-edge measurement is compromised at the source** (this gates the 08-05 kalshi_llm test).

---

## ROOT CAUSE (evidence-backed)

**The resolver classifies Kalshi `finalized`-with-result markets as `pending` and never books them, because the fetch path (`pykalshi` `get_market`) returns an empty `result` field until a market reaches `settled` — which for these markets is delayed weeks-to-indefinitely.**

Chain of evidence:

1. **The stuck markets ARE resolved on Kalshi (public API, $0, unauthenticated):**
   - `KXCPICOMBO-26JUN-0202` (exp 07-14): `status=finalized, result=no`
   - `KXCBDECISIONKOREA-26JUL15-H25` (exp 07-16): `status=finalized, result=yes`
   - `KXCBDSA-26JUL23-H25` (exp 07-23): `status=finalized, result=no`
   - `KXATTENDUFC250-26JUN-JSTA` (exp 06-15): `status=finalized, result=no`
   - **All carry a definitive `result` but `settlement_timestamp=None`** (finalized ≠ settled).

2. **The resolver reaches them and get_market SUCCEEDS — but returns them `pending`.** From the engine's own hourly tick logs (`journalctl -u trading-corp`):
   ```
   kalshi_resolver tick: scanned=108, resolved=0,  pending=101, void=0, not_found=0, errors=0
   kalshi_resolver tick: scanned=108, resolved=13, pending=88,  ...  not_found=0, errors=0
   kalshi_resolver tick: scanned=108, resolved=2,  pending=99,  ...  not_found=0, errors=0
   ```
   `not_found=0` + `errors=0` every tick ⇒ get_market is NOT 404ing or throwing. `pending≈100` ⇒ `get_market_resolution` returned `status='pending'`, which per `brokers/kalshi.py:349-367` happens **only when `m.result` is empty**. So pykalshi returns the market with `result=''` for markets the public API reports as `finalized`-with-result.

3. **Booking is effectively stalled for kalshi_llm:** **2 round-trips booked in the last 10 days** (both 08-01, both pre-epoch backlog), against **1,497 unresolved / 1,137 past-expiry**. The resolver keeps re-scanning the same ~100 pending rows every hour and booking almost nothing.

**Why `pending` (not `not_found`) matters:** because the resolver treats them as pending, it *correctly* keeps retrying — but the retry never succeeds while the market sits in `finalized`-not-`settled`, and there is **no give-up / no finalized-result fallback**. The decided outcome (immutable at `finalized`, available on the public API) is simply never read.

---

## COMPOUNDING DESIGN FLAW — head-of-line starvation

`kalshi_resolver._fetch_unresolved_orders`: per-actor budget **50 rows/cycle**, ordered `expires_at ASC`. Two problems stack on the root cause:

- **Stored `expires_at` is fake-early for long-horizon markets.** Sample of the earliest-queued unresolved markets vs their REAL Kalshi close:
  | ticker | stored expires_at | real close (Kalshi) | status |
  |---|---|---|---|
  | KXSPACEDATACENTER-26-35JAN01 | 2026-06-03 | **2035-01-01** | active |
  | KXSPACEDATACENTER-26-31JAN01 | 2026-06-03 | **2031-01-01** | active |
  | KXANTHROPICRESCIND-26JUN-27JAN01 | 2026-06-02 | **2027-01-01** | active |
  | KXMIFEPRISTONEMAIL-26-27JAN | 2026-06-08 | **2027-01-01** | active |
  | KXCONGRESSTRADES-25-DROU | 2026-05-15 | **2026-12-31** | active |
  These resolve in 2026-12 → 2035 but carry May/June-2026 stored expiry, so they sort to the FRONT of the queue and are genuinely `pending` (correctly) for months-to-years.
- **Front-40 distinct blocked markets: 8 perma-active + 32 `finalized`-but-stuck.** The finalized ones are the root-cause victims (`result` not surfaced). Because neither the perma-active nor the finalized-not-settled rows ever book, they **permanently occupy the 50-slot budget** — nothing behind them is reached.
- **Queue depth ahead of the 08-05 2Y-Treasury markets: 1,213 rows / 196 markets** (stored expiry < 08-05).

---

## IMPACT — the 08-05 forward test will NOT land cleanly

The 2Y-Treasury-FOMC markets finalize 08-05 with a definitive result. By the identical mechanism, pykalshi will very likely report `result=''` (`pending`) → the resolver will not book them → **no kalshi_llm round-trip appears** → the dashboard stays "0 resolved" and the forward-edge measurement never materializes. So the division's "0 resolved" is **partly this resolver gap, not purely a young epoch window.** Any forward-edge read must source outcomes from Kalshi's PUBLIC API (finalized results), not from `kalshi_round_trips`, until this is fixed.

**Scope note:** kalshi_arbitrage DID book 12 today (farm bill), so the resolver works for markets that reach `settled`. The gap bites markets that linger in `finalized`-not-`settled` — which dominate the kalshi_llm politics/novelty book.

---

## OPEN QUESTION (needs a live probe — NOT run here)

The telemetry proves get_market returns `result=''` for finalized markets, but the *reason* is inside third-party `pykalshi` (not in the repo). Recommended next read-only diagnostic (one live call on prod, in the engine venv): `await broker.get_market_resolution('KXCBDSA-26JUL23-H25')` and inspect the raw pykalshi MarketModel — to confirm whether pykalshi (a) hits a stale base URL/version, (b) only exposes `result` at `settled`, or (c) parses a field the current API renamed. That determines the fix (broker reads public-v2 `result` at `finalized`, vs pykalshi upgrade, vs a finalized-result fallback + a give-up/skip for long-horizon rows so they stop clogging the budget).

**Not fixed this session, per instruction. Diagnosis only — decision is the operator's.**

*Guardrails: read-only; public API only ($0); no code/config/roster/DB/deploy change; no edge/prospect memory.*
