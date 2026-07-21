# kalshi_arbitrage — data review (post 2026-07-07 resolver leg_date fix)

**Date:** 2026-07-21 · **Mode:** READ-ONLY · Data only; no edge/viability/prospect verdict (operator's call). Fee = Kalshi `ceil(0.07·C·P·(1−P))` per side (entry only; settlement free). Slippage = 1¢/contract entry (paper fills at mid). `realized_pnl` stored GROSS.

> **Headline:** the +$3,040.15 is a **compressed-settlement backlog drain**, not measured forward edge. **100% of the 260 resolved round-trips were ENTERED pre-fix (2026-05-16 → 06-29); ZERO post-07-07 entries have resolved.** The forward-edge subset (entered AND resolved post-fix) = **0**. 97% of the P&L booked on a single day (07-07), the day the resolver drained 2 months of backlog.

## STEP 1 — Timeline
| Split | n | WR | gross | net (fee+slip) |
|---|---|---|---|---|
| (a) pre-07-07 entries | **260** | 79.2% | **+$3,040.15** | +$2,987.97 |
| (b) post-07-07 entries (entered+resolved) | **0** | — | **$0** | $0 |

- Entry span: **2026-05-16 → 2026-06-29** (all pre-fix). Resolved span: 2026-07-07 15:46 → 07-18.
- Resolved by day: **07-07 = 242 RT / +$2,947.87 (97%)**; then 07-14 (12/−$10.51), 07-15 (4/+$104.79), 07-18 (2/−$2.00). Every post-07-07 resolved row was **entered in May–June** (trickle is also backlog).
- (b) is not "small" — it is **empty**. No forward-edge conclusion is possible from resolved data.

## STEP 2 — Sanity on +$3,040.15
- **Reconciles to the DB exactly:** 260 / 206W / 54L / **+$3,040.15** gross. Fees −$11.53, slippage −$40.65 → **net +$2,987.97**.
- **Sizing:** flat **~$1 notional per leg** (total notional $260 across 260 RT; avg 15.6 contracts/RT; avg entry 0.422). Per-RT $11.69, per-contract $0.75.
- **Mechanism of the P&L:** cheap long-shot legs winning. Top gainer = NO @ **$0.01 × 100 contracts = $1 stake → +$99** (FDA/gedatol). "Beef tariff" set = NO @ $0.02 × 50 → +$49. Losers = the full $1 stake (−$1.00 each). So the return is a **$1-stake × occasional 50–100× long-shot** profile, not risk-free arb locks.
- **Concentration:** no single RT > 5% of total realized (top = **3.3%**, the +$99). But the +$99 winner = **18.6% of division equity** — the total is long-shot-driven, not broad.
- **Fees:** minimal (cheap legs → low `C·P·(1−P)`). **Slippage:** −$40.65 modeled at 1¢/contract on 4,065 contracts — meaningful because cheap legs carry high contract counts (same cheap-leg drag flagged for kalshi_llm). ★ **Paper→live gap (see §6):** on a $0.01 NO the 1¢ tick = 100% of leg price; real Kalshi book depth at the extreme tick is thin, so filling 100 contracts @ $0.01 live is unlikely — the paper number is optimistic for exactly the legs that drove it.

## STEP 3 — Per-arb-type
| arb_type | strategy | n | WR | gross | net |
|---|---|---|---|---|---|
| temporal | kalshi_temporal_bucket_arb | **260** | 79.2% | +$3,040.15 | +$2,987.97 |

100% temporal-bucket arb. Zero `tail_price` arb resolved. All of it is backlog, so "edge concentration" is not attributable to forward performance yet.

## STEP 4 — Post-fix time series
| Day | resolved | W | gross |
|---|---|---|---|
| 2026-07-07 | 242 | 201 | **+$2,947.87** |
| 07-14 | 12 | 1 | −$10.51 |
| 07-15 | 4 | 4 | +$104.79 |
| 07-18 | 2 | 0 | −$2.00 |

**Classic backlog-drain signature:** one massive settlement day (97% of P&L) coincident with the resolver fix, then a near-flat trickle (+$92 total, all from pre-fix entries). **Not** steady forward accumulation.

## STEP 5 — Open positions (231)
- **227 pre-fix legacy** + **4 post-fix entries** = 231. (Post-fix new-logic entries so far: FOMC-guidance ×2 [07-12], Díaz-Canel departure ×2 [07-11] — all still open.)
- `expires_at` = **null for all 231** — temporal/bucket rows carry `leg_date`, not `expires_at`; resolution timeline is **not knowable from this field** (instrumentation gap; the leg_date-based resolver is what the 07-07 fix repaired).
- **No open position > 5% of division equity** (all ~$1 notional; $1/$532.84 = 0.19%).
- **Unrealized MTM not computable** from stored data (needs live Kalshi bids) — flagged, not chased.
- New-logic forward entry rate is **very low** (4 in ~2 weeks, post the 60-day horizon caps from the same deploy) → a forward sample will accumulate slowly.

## STEP 6 — Live-mode readiness (surface only, no recommendation)
- **Forward-edge sample = 0 resolved / 4 open** → the +$3,040 says nothing about live-executable edge. What would need to be true first: a **meaningful post-fix entered-and-resolved sample** (e.g. n≥30) that is **net-positive after fees AND realistic slippage/liquidity**.
- **Fill model gap (strategy-specific):** paper fills at **mid**; the P&L is concentrated in **extreme-cheap legs ($0.01–$0.02)** where live crossing cost + thin book depth are largest — this is the strategy most exposed to a paper→live gap.
- **Account isolation:** kalshi_arbitrage shares one KalshiBroker paper account with kalshi_llm_arbitrage (see §7) — P&L is **commingled**; per-division equity attribution would be needed before live capital.
- **Safety state:** `auto_execute:false` + `standby:true` (kill-switch = not armed). No autopause on arb divisions (autopause is copy-trading only). No feed-health breaker (Kalshi market-data, not a scraped feed).

## STEP 7 — Dashboard ambiguity (diagnosis + fix plan; no code this session)
**Root cause (deeper than labels):** `kalshi_arbitrage` and `kalshi_llm_arbitrage` both use **`broker: kalshi`** → the **same KalshiBroker paper account** → identical equity **$532.84** (commingled). `kalshi_crypto`/`kalshi_weather` use dedicated `broker: paper` ($500 each), so they differ. The equity card for either arb/llm division shows the **shared account**, not a per-division figure.

Proposed follow-up work item (later):
1. **Division short-code + color on key stat cards** (e.g. `LLM` vs `ARB` chip, distinct accent color) so the selected division is unmistakable at a glance.
2. **Warning banner when the dropdown value ≠ the URL fragment** (the operator's earlier confusion was a wrong-dropdown read).
3. **Surface the broker/account identifier on the Equity card** (e.g. "shared Kalshi paper acct") so identical equity across two divisions is explained rather than confusing — and file the deeper item: **per-division P&L attribution** for divisions that share one paper account (equity/Today's-P&L can't currently be attributed to arb vs llm).

Package as a follow-up; not this session.

## Data-quality gaps flagged
- Forward sample empty (0 post-fix resolved) → forward edge unmeasured.
- `expires_at` null on temporal/bucket rows → open-position resolution timeline not derivable from that field.
- Unrealized MTM needs live bids (not stored).
- Shared paper account → arb vs llm P&L commingled (no per-division attribution).
- Cheap-leg paper fills (mid, $0.01–0.02) likely unachievable at size on the live book — paper P&L optimistic for the drivers.

*Guardrails honored: read-only; no code/config/roster changes; no live recommendation; no dormant features enabled; no memory written; stopped at findings.*
