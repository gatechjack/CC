# Polymarket option (c) Phase 2 — validation (watch_only seed → REDEEM-grounded realized)

**Branch:** `polymarket-option-c-phase2-2026-06-10` (commits `b121e25`, `b8cde0c`) — unmerged.
**Base:** `origin/main` `c8d3902` (post Phase-1 merge).
**Date:** 2026-06-10. Read-only validation; no prod write, no refresh run.

## What changed
`seed_polymarket_watchlist_deep.py` (the Sunday `watch_only_whales` population job)
now computes PnL on the Phase 1 REDEEM-grounded realized basis
(`build_audit_report` + `score_whale_from_audit`) over the windowed RAW fills,
instead of naive held-to-resolution (`compute_polymarket_stats`). It also carries
the exhaustion walk + `window_truncated` flag (b44e3ed/f448c93) into the seed.

**Behavior boundary held:** still writes `watch_only_whales`, still weekly, still
display-only; cadence / output key / record contract (additive only) / promotion
path / service / timer all unchanged.

**Operator decisions honored:** (1) swap + carry the exhaustion fix; (2) FLAG-ONLY
for both `window_truncated` and `pnl_inflation_ratio` — surfaced as additive
fields, roster membership stays governed by the existing n/recency/wr/pnl floors;
the scorer's exclusion gate is NOT a roster filter here.

## Tests
- `tests/test_polymarket_watchlist_seed.py`: **37 passed** (rewrote the
  `compute_polymarket_stats` spy → `build_audit_report` spy; added a
  realized-vs-naive proof, a flag-only truncation test, and a fetch_error→
  truncated test). Held-to-resolution fixtures gain a REDEEM leg per winning
  decision (central `_with_redeems` in `_run_seed`) so realized == the naive
  numbers they assert; also fixed a latent fixture bug (size override left
  `usdc_size` stale).
- Full suite: **28 failed = the documented 28 known-fails baseline, zero new,
  zero polymarket/seed**. 3 pre-existing collection errors (bitunix /
  backtest modules absent at this SHA — verified identical on clean `main`,
  out of scope). The failed set (28) and error set (3) are unchanged from the
  baseline; the only passed-count delta is +3 net new seed tests.

## Live dry-run (illustrative sample: candidates=8/category, 39 unique, max-pages=10)
NEW = exhaustion + realized; OLD = current main (naive + 150 early-stop).

| run | candidates | survivors | n_floor | wr_floor | pnl_floor | truncated |
|-----|-----------:|----------:|--------:|---------:|----------:|----------:|
| OLD (naive/150-stop) | 39 | 1 | 23 | 9 | 6 | n/a |
| NEW (realized/exhaust) | 39 | 2 | 5 | 25 | 7 | 0 |

**Zero survivor overlap.** The exhaustion walk slashes `n_floor` drops (23→5):
the 150-stop was starving windows below the min-decision floor. `wr_floor` rises
(9→25): a fuller 100-decision window exposes more whales whose true recent WR is
below 0.62.

### Headline mover — "Latina" (current naive #1)
| basis | window n | PnL |
|-------|---------:|----:|
| OLD naive, 150-stop | 11 | **+$365,929** (ranked #1) |
| exhaustion, naive held-to-resolution | 100 | **−$404,598** |
| exhaustion, REDEEM-grounded realized | 100 | **−$326,392** |

The 150 early-stop truncated Latina's window to its most-recent ~11 decisions
(lucky winners) → ranked #1 at +$366k. The full 100-decision window shows a
deeply negative whale. **Current production ranks a losing whale #1**; Phase 2
correctly drops it. Attribution: dominant cause is the **exhaustion fix**
(window 11→100 reveals the losses); the realized swap is a smaller secondary
adjustment (−$404k naive → −$326k realized over the full window).

### Newly surfaced (entered under realized basis)
- **zthunderfury**: naive windowed +$4,637 (below the $5k floor → fails the
  naive screen) but REDEEM-realized **+$32,949** (inflation −6.1: held-to-
  resolution *understated* it). n=16 (provisional). Realized surfaces a genuinely
  profitable whale the naive screen missed.
- **superblueinc**: naive +$19,851 vs realized +$17,616 (inflation 0.11, modest);
  both positive, clean. Added once exhaustion gave it a full n=100 window.

### /closed-positions cross-check (Polymarket's own resolved-position feed)
`/closed-positions` is **positive-only** (true losses never appear) and capped
~50 rows — which is exactly why the seed reconstructs PnL from `/activity`
instead. Consistent with that: Latina's `/closed-positions` shows +$2.16M over
the window's condition-ids while the audit's true net realized is −$326k (the
losses are invisible to `/closed-positions`). For superblueinc/zthunderfury the
positive-only sums exceed the audit's net realized, same direction. The cross-
check confirms the realized basis captures losses that `/closed-positions` (and
the naive basis) structurally cannot.

## Bug found and fixed during validation (`b8cde0c`)
`_fetch_wallet_activity_windowed` returns partial rows + term `fetch_error` on a
mid-walk API error — an incomplete window, same as the page ceiling. The initial
Phase 2 seed flagged only `max_pages_hit`, so a fetch-errored whale's
floor-bounded realized would be trusted silently. Fixed to
`term_reason in (max_pages_hit, fetch_error)`, mirroring the refresh; new test added.

## Caveats
- Dry-run is a **small illustrative sample** (candidates=8/category); production
  runs candidates=500 — the survivor set will be larger and may include
  high-volume whales that truncate at max-pages=10.
- NEW and OLD dry-runs ran minutes apart against live data — minor candidate
  drift possible; the qualitative conclusion (naive/150-stop over-states) is
  robust.

## Disclosure (per 82fda13)
All work local in the dedicated worktree. No prod write, no `watch_only_whales`
refresh executed, no SSH/prod mutation. Live dry-runs hit only free public
Polymarket read endpoints (`/leaderboard`, `/activity`, gamma `/markets`,
`/closed-positions`). Branch pushed unmerged; operator runs the merge.
