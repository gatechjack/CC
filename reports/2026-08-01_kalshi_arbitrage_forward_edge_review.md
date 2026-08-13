# kalshi_arbitrage — forward-edge review (dashboard now +$3,476 / 79% WR / 272 resolved)

**Date:** 2026-08-01 · **Mode:** READ-ONLY (no code/config/roster/DB/deploy changes; artifact uncommitted per session guardrail).
**Division:** `kalshi_arbitrage` · Strategy `kalshi_temporal_bucket_arb` · Karen keypair (`secret_ref: kalshi_karen`), equity ~$507.98.
**DB:** `/home/azureuser/trading_corp/data/trading_corp.db`, opened `-readonly`. Reconciles to dashboard exactly: **272 resolved / 216 won (79.4% WR) / +$3,476.19 gross**, 100% `arb_type=temporal`.
**Fee model (confirmed in code, per-order):** `math.ceil(0.07·C·P·(1−P)·100)/100` per side, entry only, settlement free (`web/data.py:5356-5360`). Slippage modeled 1¢/contract, paper fills at mid.

---

## BOTTOM LINE (the two numbers that decide it)

1. **Backlog vs forward split: 272 pre-07-07 entries / 0 forward. Forward subset is STILL empty (n=0), unchanged from 07-21 and 07-26.** Every one of the 272 resolved round-trips was entered **2026-05-16 → 2026-06-29**. Not a single resolved trade was entered on/after 07-07. The pre/post-fix entry split is **100% / 0%**.
2. **Concentration: 62.8% of all P&L is one event (KXFDAAPPROVE-GED, $2,184.14). Top 3 events = 86.1%. One settlement day (07-07) = 84.8%.** This is event-cluster settlement, not steady accumulation.

**The +$3,476 is 100% backlog / event-cluster drain. It says nothing about forward, live-executable edge. The forward question remains unanswered — same conclusion as the 07-21 review, now with +$436 more backlog.**

---

## STEP 1 — Backlog vs forward split (load-bearing)

| Split | n | WR | gross | net (fee+slip) |
|---|---|---|---|---|
| **(a) pre-07-07 entries (backlog)** | **272** | 79.4% | **+$3,476.19** | +$3,418.76 |
| **(b) post-07-07 entered AND resolved (forward)** | **0** | — | **$0** | $0 |

- Entry span of all 272: **2026-05-16 → 2026-06-29**. `MAX(entry_ts)` across the entire resolved set = `2026-06-29T22:10:16`. The `CASE WHEN entry_ts<'2026-07-07'` split returns 272 pre / 0 on-07-07 / 0 post.
- (b) is not "small" — it is **empty**, for the third review running. No forward-edge conclusion is possible from resolved data.

**Concentration by event (top of 272):**

| event_ticker | n | won | gross | % of total |
|---|---|---|---|---|
| KXFDAAPPROVE-GED | 73 | 63 | +$2,184.14 | **62.8%** |
| KXFARMBILL-26MAY | 6 | 6 | +$427.33 | 12.3% |
| KXBEEFTARIFF-26MAY | 16 | 8 | +$384.00 | 11.0% |
| KXFISAEXTEND-26MAY | 18 | 10 | +$224.40 | 6.5% |
| KXKNESSET-27 | 6 | 3 | +$144.00 | 4.1% |
| *(remaining ~30 events)* | — | — | +$112.32 | 3.2% |

- **Single largest event = 62.8%.** Top 3 = **86.1%.** Top 5 = **96.7%.**
- **Concentration by settlement day:** 07-07 (backlog-drain day) = **$2,947.87 = 84.8%** of all P&L. 08-01 = $436.03 = 12.5%. 07-14/07-15/07-18 trickle = +$92.28.

**The "new since last review" (08-01) is a second mini-drain, not forward accumulation.** All 12 rows that resolved 08-01 were entered in **May**:

| entered | event | side | qty | entry | pnl |
|---|---|---|---|---|---|
| 2026-05-23 | KXFARMBILL-26MAY | no | 100 | $0.01 | +$99.00 (×4) |
| 2026-05-22 | KXFARMBILL-26MAY | no | 16.7 | $0.06 | +$15.67 (×2) |
| 2026-05-16→22 | KXDIAZOUT-MDC | no | 3-4 | $0.23-0.32 | +$2-3 (×4) |
| 2026-05-16/17 | KXDIAZOUT-MDC | yes | 2.3 | $0.43 | −$1.00 (×2) |

The four "farm bill $0.01 NO +$99" rows from the screenshot are real — **all entered 2026-05-23**, each its own `arb_set_id` but all the same event+side, settling together 08-01. **Farm bill alone = $427 of the $436 (98%) that resolved 08-01.** Screenshot signature (correlated positions on ONE event settling together) **confirmed, not refuted.**

---

## STEP 2 — Forward entry rate (dormancy check)

Entry signal = `would_have_placed` audit event (paper; `standby:true`, so this is the "would place" signal, not a fill).

| Window | `would_have_placed` emissions |
|---|---|
| 07-06 | 16 (KXFDAAPPROVE burst) |
| 07-07 | 8 |
| 07-11 | 2 · 07-12 | 2 |
| **07-13 → 07-27** | **0** |
| **07-28** | **2** (single isolated blip) |
| **07-29 → 08-01** | **0** |

- **Entry activity has NOT meaningfully resumed.** Since the 07-26 review there have been exactly **2 entry signals (07-28), then silence** — ≈0.09 entries/day. The 07-26 report's "zero since 07-13" is technically superseded by the 07-28 blip, but the substance is unchanged: the division is essentially dormant.
- **Pipeline is alive**: `kalshi_temporal_bucket_scan` + `kalshi_discovery_refreshed` current to **2026-08-01T15:32** (~270 cycles/day). It is scanning and finding almost nothing that clears the fee floor — the supply exhaustion diagnosed 07-26 persists.
- **Post-fix pending (open, unresolved) entries:** 07-11 (2) + 07-12 (2) + 07-28 (2) = **6 emissions**, none resolved yet (consistent with `MAX(entry_ts)=06-29` in the resolved set). These are the only candidates that could ever populate subset (b) — 6 of them, versus the n≥30 trigger.
- **Answer:** the 272-resolved is the SAME backlog finally settling; there is essentially no new supply behind it.

---

## STEP 3 — Net-of-fee reality

- **Fee model confirmed per-order** (not per-contract): `ceil(0.07·C·P·(1−P))` with C = whole-order contracts inside the ceil. On a $0.01 NO / 100-contract leg → ceil(0.07·100·0.01·0.99·100)/100 = **$0.07** entry fee.
- **Full set net = +$3,418.76** — gross +$3,476.19, fees **−$12.25** (immaterial), slippage **−$45.18** (1¢/contract on 4,518 contracts; meaningful only because cheap legs carry high contract counts).
- **Forward subset (b) net = $0** (n=0 — nothing to compute). This is the number that matters and it is empty.
- **Paper, not live:** `standby:true`, no `auto_execute`/halt override in `agent_state`, audit kind is `would_have_placed` (never `filled`), Karen equity static (~$507.98, unchanged since 07-21). **The +$3,476 is paper P&L — a ceiling, not realized money.**
- **Paper→live fill caveat (largest for this division):** 239 of the wins are NO legs, the P&L drivers priced **$0.01–0.06**. Paper fills at mid with infinite depth; the live Kalshi book at the extreme $0.01 tick is thin, so filling 100 contracts @ $0.01 live is unlikely. The +$99-type wins **assume fills the live book may not provide** — the paper number is optimistic for exactly the legs that produced it.

---

## STEP 4 — Concentration / correlation risk

- **Yes, the strategy takes many correlated legs on one event.** KXFDAAPPROVE-GED = **73 legs** (the 265-strikes-on-one-event pattern). Farm bill = 6 legs, all NO, all one event/direction, each a separate `arb_set_id`.
- **But the downside is bounded and small in dollars.** Sizing is flat ~$1 stake/leg. For a cheap NO leg, max loss = the $1 stake. **Max total stake on any single event = $73 (KXFDAAPPROVE-GED) = 14.4% of the $507.98 equity.** A wrong event view on GED would have cost ≈$73 (−14% equity), **not zeroed the account.**
- So the concentration here is **P&L-attribution concentration** (a handful of long-shots hitting made the money → not repeatable), **not catastrophic single-event loss risk.** That is the honest framing: the risk is that the number won't recur, not that one event blows up the book.

---

## STEP 5 — Verdict (data only)

- **Is +$3,476 forward-repeatable edge?** No. It is **100% backlog / event-cluster settling out.** 62.8% from one event, 86.1% from three, 84.8% from one day. Forward subset = 0.
- **Is the division actively finding new opportunities?** No — **dormant with settlements trickling.** 2 entry signals in the 3 weeks since the last review; the scanner runs healthy but the fee-clearing edge supply that KXFDAAPPROVE-GED provided has not returned.
- **Does subset (b) clear n≥30 net-positive-after-fees?** No — **(b) = 0.** The pending forward candidates number 6 (07-11/12/28 opens), not 30. Forward edge remains **unproven**, same as 07-21 and 07-26.
- **What would need to be true to call this a real division (vs a one-time drain):**
  1. A **sustained entry rate** — supply of fee-clearing (≥4¢ temporal / ≥5¢ bucket) edges returning across multiple events, not a single 07-28 blip.
  2. A **post-06-29 entered-AND-resolved sample of n≥30** that is **net-positive after fees AND realistic live fills** (crossing cost + thin-book depth at the $0.01–0.06 ticks that drive the P&L).
  3. Confirmation the edge survives **outside the KXFDAAPPROVE-GED-style rich single series** — i.e. it is not one recurring event type doing all the work.

Until then: **still mostly backlog, forward edge unproven.**

---

*Guardrails honored: read-only; no code/config/roster/DB/deploy changes; no live recommendation; no edge/prospect verdict written to memory (operator's call); per-order fee model used; artifact left uncommitted.*
