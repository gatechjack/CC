# PM open tickets / findings filed 2026-08-27 (NOT to fix now — recorded so they aren't lost)

## TICKET T1 — `/positions` default page cap — REPRIORITIZED: LOAD-BEARING FOR LIVE (and it is FIXABLE)
**Filed 2026-08-27 (poller run); reprioritized + updated 2026-08-27 (exit-detection probe).** `cap_suspect` fired on
**6 of 14 whales** — each returned **exactly 100** `/positions` rows (SDTrading, 4751346, 0x71edffd0, MadeiraIsland,
BetMechanic, kutsumiakia). The shared `fetch_positions` sends only `user` (no `limit`), so the poller sees only the
default page.

**★★ REPRIORITIZED (Jack, 2026-08-27) — no longer a stats-quality item; LOAD-BEARING FOR REAL MONEY.** Under a
copy-EXIT rule, a position dropping off page 1 *because the whale OPENED something bigger* would trigger a **REAL
SELL order dumping a REAL position** — on exactly our highest-volume whales. Presence-on-the-default-page is NOT a
safe exit signal.

**★ GOOD NEWS — measured 2026-08-27, the cap is a SOFT DEFAULT, not a hard limit; it is paginable:**
- `/positions?user=X&limit=500` **returns 500** (vs 100 default); `limit=1` returns 1 → **`limit` is honored.**
- SDTrading: default **100**, but `limit=500`+`offset=100` reveals **~514** open positions (default showed ~19%).
  BetMechanic: default 100, `limit=500`=500, `offset=500`=500 → **>1000** positions. MadeiraIsland: 157 (default
  showed 100).
- **The default 100 = the LARGEST 100 by size.** BetMechanic default min size **3698**; `limit=500` min **421** →
  small positions are simply below the top-100 and invisible by default. A position ranked ~100 gets bumped below
  the fold when the whale opens bigger ones → **looks vanished while still held** = the false-exit mechanism.
**Fix direction (NOT to implement here):** page the FULL open book (`limit=500` + `offset` for whales >500) for both
paper capture AND any live exit rule; never trust the default page. This removes the top-100 false-exit cause
entirely. Still the same *class* as the `/closed-positions` loss omission (a feed under-representing the biggest
accounts), but — unlike that one — **recoverable with paging.** Its own ticket; do not chase now.

## TICKET T2 — tier-2 poller categorization gap: PRIORITY LOWERED (measured ~0 miss in practice)
**Revision, 2026-08-27.** The tier-1-only poller gap was earlier estimated at **~6.3%/7d** (soccer/tennis game
lines wrongly → unknown). The Rung-3 step-2 run **measured it at ~0 this snapshot**: of 90 skipped positions, **85
futures/novelty + 4 novelty + 1 false-positive (cycling *tournament-winner* futures) → ZERO trackable game/match
lines skipped.** The whales' entire off-tile open book is long-dated futures/politics.

**Effect:** the 6.3% was an **upper bound over resolved 7d activity**; live open-book snapshots run far below it. This
**LOWERS the tier-2 ticket's priority** — it is not silently losing trackable game lines at the estimated rate.
**Record so the 6.3% is not treated as current.** (Ticket stays open — a poller tier-2 fallback / missing prefixes
would still be correct — just de-prioritised.)

## DESIGN QUESTION D1 — whale-exit disposition (AWAITING JACK'S RULING)
**Raised 2026-08-27, Rung-3 step-3.** `adjudicate()` scores a vanished (whale-exit) position off **gamma's market
resolution**, never off the whale's exit — so a whale who sells pre-resolution is booked on the eventual **market
outcome** (or `stale`/no-P&L if the market never resolves past `end+72h grace`). The copy-trade thesis ("follow the
whale in AND out") would instead book at the **exit price** when the whale sells. **This is a design change and Jack's
to rule** (full cost/alternative in `STAGE1_RUNG3_ADJUDICATE_ROLLUP_2026-08-27.md`). Not implemented.

## E1 — WHALE-EXIT DETECTION (read-only investigation, 2026-08-27) — findings for Jack's design ruling
Real Polymarket `data-api` GETs (SDTrading/MadeiraIsland/BetMechanic/4751346, the capped whales). No writes.
Runners `cc\pm_exitdetect_probe.sh` / `pm_exitdetect_probe2.sh`. **Jack's split kept: PAPER=exit PRICE, accuracy>latency,
`/activity` OK; LIVE=exit SIGNAL, latency>accuracy.**

**Q1 — Is there anything PUSH-based? NO per-wallet push; polling is the only option.** The shared client is 100% httpx
GET polling — no stream. Polymarket's only realtime surface is the CLOB websocket, whose channels are **market**
(order-book/trade prints for a market — not per-wallet position state, and not a feed of "wallet W exited") and
**user** (authenticated with the *account's own* L2 API creds → only YOUR orders; you cannot subscribe to an
arbitrary whale's fills without their key). No webhook. (The WS host `ws-subscription-clob.polymarket.com` did not
even resolve from the box — egress/DNS — but that is moot: no third-party-wallet push exists regardless.) **Polling
floor:** 14 whales × 1–2 `/positions` calls (full-book paging) ≈ 14–30 req/cycle; no 429s at this volume; sub-minute
(15–30 s) polling is comfortably within limits. The real floor is the **data-api's own indexing lag** after a whale's
on-chain sale (seconds-to-tens-of-seconds), not our request budget.

**Q2 — Does `/positions` return SIZE? YES, on every row** (`size`, contracts; + `avgPrice`, `curPrice` mark,
`redeemable`). So a **partial exit is a size DIFF**, not a disappearance — *provided the position stays on the page
we fetch*. **BUT** the default page is **top-100 by size**, so a small (or bumped-below-100) position vanishes while
still held (see T1). With full paging a partial close is visible as a shrinking `size`; the poller already has the
size-reduction branch to read it.

**Q3 — Can the 100 cap be paged past? YES (see T1).** `limit=500` returns 500; `offset` walks further; the full open
book is retrievable (SDTrading ~514, BetMechanic >1000, MadeiraIsland 157). The cap is a default-param artifact, not
a hard limit. **This is the single most important finding — it makes both a completeness gate (paper) and a
trustworthy presence signal (live) achievable without a new endpoint.**

**Q4 — What does `/activity` return for a SELL?** Per **fill**: `{timestamp, side:'SELL', type:'TRADE', size, price
[0-1], usdcSize, conditionId, outcome, transactionHash}`. Real example (MadeiraIsland): `SELL size 562.38 @ 0.79,
usdcSize 439.62, cid 0x26f093…, 'Clara Tauson'`. **Per-fill, not per-exit:** one market exit = many SELL rows (that
one cid had **43** SELL fills). So "the whale exited market X" = aggregate SELL rows by `conditionId` over a window;
**partial vs full = reconcile Σ(SELL size) against the held size** (`/activity` alone can't say full-vs-partial).
**Exit style is whale-dependent:** MadeiraIsland/4751346 SELL heavily (80–89 sells/200); SDTrading/BetMechanic exit by
**`REDEEM`** (hold-to-resolution, no exit-price decision — that's just resolution, already handled by gamma).
**Truncation is HARD at offset 5000** (`offset=6000` → HTTP 400 "max historical activity offset of 5000 exceeded").
For *recent* exit detection this is fine (exits are at the top); for full history it loses the >5000 tail. **Shared
plumbing with Stage 5: YES** — Stage 5's loss-completeness was already going to use `/activity` per-fill (P1's reason
`/closed-positions` omits held losses); the paper exit-price reconciliation uses the same endpoint + hits the same
5000 cap. Build them on one `/activity` fetch/parse layer.

**Brief findings (not a design):**
- **Live detection latency:** poll-only → ≈ poll interval (can be 15–30 s) + data-api indexing lag = **tens of
  seconds to ~2 min**, never sub-second. We exit later/worse than the whale by construction; acceptable per "we fill
  at whatever the book gives us," but latency is real.
- **False-exit worst case:** a naive presence rule on the default page sells a real, still-held position into the
  book (spread + slippage + fees), then re-buys worse when it reappears — a round-trip loss for nothing, concentrated
  on the 6 highest-volume whales. **Full-book paging (Q3) removes the dominant false-exit cause;** remaining ambiguity
  (sold vs redeemed) is resolvable because redeemed = market resolved = we'd resolve too.
- **Cheap PAPER-only interim (no new endpoint):** book the vanished position at the **last-observed `curPrice` mid**
  from the prior poll. What it gets wrong: (a) mid ≠ the whale's actual fill (they may cross the spread); (b) can't
  tell sold-vs-redeemed (a resolved position's mark is 0/1, not an exit); (c) stale by up to one poll interval. Good
  enough as a *screening* number (kills the worst hold-to-resolution bias); the **honest** paper answer is `/activity`
  SELL reconciliation, which Jack already accepted.
- **Could not establish read-only:** the CLOB WS reachability/subscribe from the box (DNS didn't resolve — egress);
  the data-api's true indexing lag (needs a live timed sale); the exact rate-limit ceiling for sub-minute polling of
  more whales; and whether a dust-sized position can drop off `/positions` entirely even with `limit=500&sizeThreshold=0`
  (no sub-1-contract test position was available).
