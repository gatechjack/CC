# kalshi_arbitrage — interim mark-to-market on PENDING forward positions
### Are the 08-12 revived temporal_bucket_arb placements trending positive before they resolve?

- **Date:** 2026-08-14 ~17:0x UTC (live Kalshi read taken mid-run).
- **Mode:** READ-ONLY. No orders, no writes, no code/config changes. Live `$0` GET only.
- **Read path:** Karen account (`kalshi_arbitrage` is Karen-isolated, `secret_ref: kalshi_karen`). Creds fetched from KeyVault `kv-tc-vtwbowt3wtkpy` via the VM's **managed identity** (`load_secrets()` → `DefaultAzureCredential` → `SecretClient`, the engine's own path); values used in-memory only, never printed. pykalshi `AsyncKalshiClient(demo=False)` → `get_market` + `get_orderbook` (a `ReadOnlyBroker`; no order methods touched).
- **This is PAPER.** Every mark is an **unrealized, mid-based paper value** — a directional hint, NOT realized P&L and NOT an edge verdict. Entry "fills" are the strategy's stored `limit_price` (paper assumption; see the 1¢ NO-leg caveat).
- **Fee model:** per-order `ceil(0.07·qty·P·(1−P))`, min $0.01. Total entry fees across the 14 pending ≈ **$0.62** — immaterial vs the marks.
- **Evidence:** `kb_mtm2.ps1` (STEP1 DB enumeration + STEP2 live Karen GET + STEP3 mark), raw output pasted into the session. Every number below traces to a shown `MKT|` line (entry from raw `audit_event.would_have_placed`; current from the live GET).

---

## TL;DR

- **No clear directional signal yet.** The script's headline **+$134.43** unrealized is a **mirage**: **+$97.00** of it (72%) comes from **two zero-liquidity phantom markets** (vol=0, oi=0, 97¢ spread — no real price), and **+$5.25** was a misread of a market that **already resolved as a LOSS**.
- **Corrected total ≈ +$31.11**, of which **+$31.50 is a single position** (`KXIPOSHEIN-DATE-26AUG22` NO leg). **Strip that one leg and the other ~11 positions net ≈ −$0.40 — flat, sitting at entry.**
- **Only ~5 of 14 positions have trustworthy quotes** (tight spread + real volume); 2 are phantom (vol=0), 3 are wide-spread/thin, 1 already resolved.
- **★ Already-resolved-but-unbooked (prominent):** `KXFOMCGUIDE-26-FWD` settled **`no`** → our YES bet **LOST (≈ −$1.07)**. This is the one *hard* datum here, and it's a loss. The resolver hasn't booked it.
- **One genuine liquid mover our way:** `KXIPOSHEIN-DATE` set — the NO-early (Aug 22) and YES-late (Sep 5) legs are *both* marking up on liquid books (the temporal set is working directionally). But it's one event, and the big number is the asymmetric 1¢-entry lottery leg, not evidence of edge.
- **Verdict:** unrealized mid-flight mark, directional hint only, dominated by illiquid/phantom marks + one lottery leg. **Real read stays resolution at n≥30.** Too early.

---

## STEP 1 — Pending forward positions (raw `audit_event.would_have_placed`, entry ≥ 07-07, not booked)

**14 pending**, all `kalshi_temporal_bucket_arb` (1 bucket, 13 temporal legs across 8 sets). Booked-and-resolved so far: 54 distinct tickers. Full enumeration with `set`/`leg`/`leg_date` is in the raw output; the 08-12 revived batch = `KXGOVTFULLFUND` (5 legs), `KXIPOSHEIN-DATE` (3 legs), `KXSP500WHEN-8000` (2 legs), `KXPRESSSECANNOUNCE` (2 legs). Two are older (`KXDIAZOUT-MDC-26SEP01` 07-11, `KXFOMCGUIDE-26-FWD` 07-12).

**Structure note:** each temporal set = a `no_early` leg (bet the event does NOT resolve by the early date) + a `yes_late` leg (bet it DOES by the later date). The `no_early` legs entered at **1¢** with **qty≈100 contracts** (~$1 stake) — cheap asymmetric lottery tickets: any favorable move produces an outsized paper mark. Weight the marks accordingly.

---

## STEP 2–3 — Per-position mark (entry → current, side math)

`cur` = current mid of the outcome side we hold (NO mid = (no_bid+no_ask)/2; YES mid = (yes_bid+yes_ask)/2 or orderbook mid). `unreal$` = qty·(cur − entry).

| # | Ticker | Side | Entry | Cur | Spread | Vol | Reliability | Dir | Unreal$ | Close |
|---|--------|------|-------|-----|--------|-----|-------------|-----|---------|-------|
| 1 | KXDIAZOUT-MDC-26SEP01 | YES | 0.12 | 0.065 | 0.015 | 151,894 | **LIVE (good)** | LOSING | **−0.46** | 09-01 |
| 2 | **KXFOMCGUIDE-26-FWD** | YES | 0.08 | — | — | 7,866 | **RESOLVED `no`** | **LOSS** | **−1.07** (realized; script's +5.25 is bogus) | 07-29 |
| 3 | KXIPOSHEIN-DATE-26AUG22 | NO | 0.01 | 0.325 | 0.01 | 4,724 | **LIVE (good)** | WINNING | **+31.50** | 08-22 |
| 4 | KXIPOSHEIN-DATE-26SEP05 | YES | 0.47 | 0.755 | 0.05 | 2,621 | LIVE | WINNING | +0.61 | 09-05 |
| 5 | KXGOVTFULLFUND-…-26SEP11 | NO | 0.01 | (0.495) | **0.97** | **0** | **PHANTOM (vol=0)** | — | ~~+48.50~~ **excluded** | 09-11 |
| 6 | KXGOVTFULLFUND-…-26SEP18 | NO | 0.01 | (0.495) | **0.97** | **0** | **PHANTOM (vol=0)** | — | ~~+48.50~~ **excluded** | 09-18 |
| 7 | KXGOVTFULLFUND-…-26SEP26 | YES | 0.53 | 0.505 | 0.05 | 2,712 | LIVE | FLAT | −0.05 | 09-26 |
| 8 | KXGOVTFULLFUND-…-26OCT01 | YES | 0.88 | 0.855 | 0.05 | 226 | LIVE (thin) | FLAT | −0.03 | 10-01 |
| 9 | KXGOVTFULLFUND-…-26OCT02 | YES | 0.89 | 0.865 | 0.05 | 300 | LIVE (thin) | FLAT | −0.03 | 10-02 |
| 10 | KXSP500WHEN-8000-26SEP01 | NO | 0.45 | 0.62 | **0.14** | 26 | **WIDE/THIN** | winning? | +0.38 | 09-01 |
| 11 | KXSP500WHEN-8000-26OCT01 | YES | 0.48 | 0.53 | 0.04 | 51 | thin | winning? | +0.10 | 10-01 |
| 12 | KXIPOSHEIN-DATE-26SEP19 | YES | 0.92 | 0.855 | 0.05 | 4,926 | **LIVE (good)** | LOSING | −0.07 | 09-19 |
| 13 | KXPRESSSECANNOUNCE-…-AUG18 | NO | 0.74 | 0.845 | **0.15** | 1,361 | **WIDE** | winning? | +0.14 | 08-18 |
| 14 | KXPRESSSECANNOUNCE-…-AUG21 | YES | 0.22 | 0.24 | **0.12** | 854 | **WIDE** | FLAT | +0.09 | 08-21 |

Side-math sanity examples (traceable to the raw `MKT|` rows):
- **#3 (NO, big winner):** entered NO @1¢ when YES was ~99%; now `yes_bid/ask = 0.67/0.68` → NO mid `(0.32+0.33)/2 = 0.325`; 100·(0.325−0.01) = **+$31.50**. Real, liquid — but it's the 1¢ lottery leg (whether 100 NO @1¢ was truly fillable is a paper assumption).
- **#2 (resolved):** `status=FINALIZED result='no'`; YES→$0; realized = 12.5·(0−0.08) − $0.07 fee = **−$1.07**.
- **#5/#6 (phantom):** `vol=0 oi=0`, `yes_bid=0.02 yes_ask=0.99` → the 0.505 "mid" is an empty-book artifact; **no reliable price** → excluded.

---

## Aggregate

| View | Total | Notes |
|------|-------|-------|
| Naive (script, 14 marks) | **+$134.43** | polluted |
| − remove 2 phantom (vol=0) marks #5,#6 | −$97.00 → **+$37.43** | no real price |
| − correct #2 resolved (+5.25 → −1.07) | −$6.32 → **+$31.11** | realized loss |
| **Corrected total (12 marks + 1 realized + resolved)** | **≈ +$31.11** | |
| **… of which #3 alone** | **+$31.50** | one liquid lottery leg |
| **All positions EXCEPT #3** | **≈ −$0.40** | flat / at entry |

Direction tally (reliable marks only, excluding phantom): a slight positive lean by count, but by dollars it is **one leg up, one resolved loss, everything else ≈ flat**.

---

## STEP 4 — Honest read

**Are the 08-12 revived placements trending positive?** Not in any reliable, broad way.
- Once the two **phantom** GOVTFULLFUND markets (+$97, no real price) and the **misread resolved loss** are removed, the entire remaining mark is **one liquid position** (`KXIPOSHEIN-DATE-26AUG22` NO, +$31.50) — and that's the asymmetric 1¢-entry lottery leg, structurally prone to big paper swings, not evidence of edge. **Every other position sits essentially at entry (net ≈ −$0.40).**

**Reliability of the mark:** poor in aggregate. Only **~5 of 14** have trustworthy quotes (tight spread + real volume: #1, #3, #4, #7, #12); **2 are phantom** (vol=0), **3 are wide-spread/thin** (#10, #13, #14 — spreads 0.12–0.15), **3 are tight-but-thin** (#8, #9, #11 — vol < 300), and **1 already resolved** (#2). A mark built mostly on empty/thin books is not a directional signal.

**Already-decided (most valuable datum):** `KXFOMCGUIDE-26-FWD` **resolved `no` → our YES bet LOST (≈ −$1.07)**. It is not yet booked in `kalshi_round_trips`. This is the one hard result in the set, and it is a loss. → **Flag for the resolver** (bucket-arb leg not booked; separately, `not_found:4` markets are stuck in the resolver too).

**Near-certain callouts:** none. No pending position is at ≥0.97 or ≤0.03 on our side — nothing has effectively decided except the already-resolved #2.

**One thing genuinely working directionally:** the `KXIPOSHEIN-DATE` temporal set — both its NO-early (Aug 22, +$31.50) and YES-late (Sep 5, +$0.61) legs are marking up on liquid books, consistent with the arb thesis (event timing moving into the [early, late] window). But n=1 event, dominated by the lottery leg.

**Bottom line — explicit:** this is an **unrealized, mid-flight paper mark — a directional hint only, NOT realized P&L and NOT an edge call.** Corrected for phantom/resolved noise it's ≈ flat-plus-one-lottery-leg, on mostly thin books, with the one settled result being a loss. **The real read remains resolution at distinct-market n≥30** (per the 2026-08-14 review). Too early to say the revived placements are winning.

---

## Appendix — reliability legend
- **LIVE (good):** two-sided, spread ≤ 0.05, vol ≥ ~1,000.
- **thin / WIDE:** tight-but-low-volume, or spread ≥ 0.10 → low-confidence mark.
- **PHANTOM:** vol=0 & oi=0 (97¢ book) → no real price; mark excluded.
- **RESOLVED:** market settled on Kalshi → use the result, not a mid (mark #2 corrected accordingly).
