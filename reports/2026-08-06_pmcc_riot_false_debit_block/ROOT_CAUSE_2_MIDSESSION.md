# PMCC RIOT "net debit" block — MID-SESSION empirical reproduction (READ-ONLY)

**Supersedes the "zero opening bid / opening rotation" theory in `ROOT_CAUSE.md` —
that theory is DISPROVEN here.** The failure reproduces mid-session with a healthy
$0.75 bid on the selected strike. Different mechanism.

- **When:** 2026-08-06, live quotes pulled **19:17–19:22Z (15:17–15:22 ET)**, market open.
- **Code:** prod-live `ef613e5` (running engine). Functions run **verbatim** in
  `repro_live_1517ET.py` (committed alongside).
- **Data source:** Robinhood MCP live quotes (read-only GETs). **Nothing placed,
  no order/consent write, auto_execute/halt untouched.**
- **Spot:** RIOT last_trade **$21.495** (bid 21.48 / ask 21.49).

## 0. Could I run the literal operator panel path on prod? No — and I say so plainly.
- Prod dashboard root returns **HTTP 302** (auth redirect); port 8000 is not publicly
  exposed; SSH is classifier-blocked for this agent. So I **cannot** invoke the live
  `/refresh-pricing` or `/pair/RIOT` render on the box or read the engine's in-memory
  `short_leg_mark`. Instead I reproduced the **exact gate computation** the panel path
  runs, against **live** quotes. Path trace confirms this is faithful (§1).

## 1. Operator path == scan path (both hit the same gate)
- **Re-analyze now** (`?force=1`) → `routes.py:978-990` → `propose_orders_for_pair(..preview=True)`.
- **Refresh pricing** → `routes.py:1032-1059` → `pmcc_pricing.price_and_stash` → `propose_orders_for_pair(..preview=True)` (`pmcc_pricing.py:124`).
- Both → `_propose_roll_short` → **`_short_roll_credit` B2 gate** (`pmcc_robinhood.py:3861`,
  block iff `conservative_net < 0`). No separate/buggier gate on the manual path.
  → answers **(d): NO**, the Re-analyze path does not use a different gate.

## 2. LIVE values logged at reproduction time (not derived)

Current short (operator's manual roll this morning; **the buyback leg**):
| leg | strike/exp | bid | ask | mark | Δ | OI | vol |
|---|---|---|---|---|---|---|---|
| **current short** | **$23.5 C 8/14** (8 DTE) | 0.66 | 0.84 | **0.75** | 0.337 | 63 | 44 |
| (this-AM's old short) | $25 C 8/7 (1 DTE) | 0.02 | 0.03 | 0.025 | 0.042 | 15185 | 1884 |

Roll-out expiry candidates (B7 forces DTE > 8 → **only 8/21** qualifies in the δ window).
8/21 chain with the **10% spread gate** applied (min_OI 100 / OI-bypass-vol 500 / spread ≤10%):

| $ strike 8/21 | Δ | bid | ask | mark | spread% | liquidity gate |
|---|---|---|---|---|---|---|
| 23.5 | 0.392 | 1.04 | 1.30 | 1.170 | 22.2% | **REJECT (spread)** |
| 24.0 | 0.355 | 0.91 | 1.13 | 1.020 | 21.6% | **REJECT (spread)** ← the δ0.35 target |
| 24.5 | 0.326 | 0.86 | 0.97 | 0.915 | 12.0% | **REJECT (spread)** |
| **25.0** | **0.292** | **0.75** | 0.82 | 0.785 | **8.9%** | **PASS** (OI 35,802) |
| 25.5 | 0.275 | 0.64 | 0.87 | 0.755 | 30.5% | REJECT (spread) |
| 26.0 | 0.238 | 0.53 | 0.69 | 0.610 | 26.2% | REJECT (spread) |

**Every near-δ0.35 strike is spread-rejected** (RIOT weeklies run 11–30% spreads mid-day);
only the very-high-OI round-number **$25.0 8/21 survives** — Δ0.29, further OTM than target.

## 3. Reproduced gate output (verbatim functions, live quotes)

**S-NEW — engine rolling the current $23.5 8/14 (mark 0.75), roll-out to 8/21:**
```
SELECTED new short: C25.0 2026-08-21 (delta 0.292, bid 0.75, ask 0.82, mark 0.785)
conservative_net = new.bid(0.75) - buyback.mark(0.75) = +0.0000
mark_net         = new.mark(0.785) - buyback.mark(0.75) = +0.0350
GATE (conservative_net < 0 ?): CLEAR (credit)   <-- lands EXACTLY on the debit boundary
```
δ0.30 (the config default, if the LLM left delta unset) selects the **same** $25.0 8/21 —
the liquid OTM pool is just {$25.0}. Same +0.0000.

**Buyback-mark sensitivity** (the value that decides block vs clear — same selected strike):
```
buyback_mark=0.66  (bid)              -> conservative_net=+0.0900 -> clear
buyback_mark=0.75  (live mark/mid)    -> conservative_net=+0.0000 -> clear (by a hair)
buyback_mark=0.785 (adj-mark rounding)-> conservative_net=-0.0350 -> BLOCKED
buyback_mark=0.84  (ask)              -> conservative_net=-0.0900 -> BLOCKED
buyback_mark=1.23  (8/05 prior close) -> conservative_net=-0.4800 -> BLOCKED
```

**S-OLD — if the engine were still rolling the $25 8/7 (mark 0.025):** over the fetched
8/14 δ-zone **every** strike is liquidity-rejected → `SELECTED: None` → a **sparse-chain**
abort, NOT a `net_debit_roll`. And even if a strike passed, 0.025 vs any positive bid is a
clear credit. **So the observed "net debit" message cannot come from the $25 8/7** — it
can only arise when buying back a **real-premium** short. This is the abort-reason
discriminator that confirms **point 4**: the engine is rolling the **$23.5 8/14**.

## 4. Does it reproduce? — plainly

- **The bug's core — strike substitution — reproduces definitively and is logged:** the
  picker selects **$25.0 8/21 (Δ0.29, bid $0.75)**, NOT the δ0.35 strike, because the 10%
  spread gate rejects the entire on-target OTM chain mid-session. Healthy bid, not zero.
- **The "net debit" verdict reproduces to the knife-edge:** at the live buyback mark
  ($0.75) `conservative_net` is **exactly $0.0000**, and it tips **negative (BLOCKED) for any
  buyback-mark read ≥ ~$0.755** — i.e. adjusted-mark rounding, the ask, or a stale
  position-scanner snapshot. The buyback mark (`PMCCPosition.short_leg_mark`,
  `pmcc_robinhood.py:2270,3775`) comes from the **position-scan snapshot**, a *different and
  potentially staler timestamp/source* than the **fresh** `get_calls_for_expiry` bid used
  for the new short — the exact temporal asymmetry that tips a genuine credit into a
  computed debit. I could not read the engine's in-memory `short_leg_mark` (prod state), so
  I cannot certify the final ±cent — but the block is real under any non-optimistic read.

## 5. Mechanism verdict (which of a–e)

- **(a) picker selected a different, far-OTM/low-bid strike — YES, PRIMARY.** $25.0 8/21
  (Δ0.29) instead of the δ0.35 ($24) target, because the **10% spread gate rejected 23.5/24/24.5**
  (all 12–22% spreads). Logged.
- **(b) old_short.mark read too high — YES, DECIDING AT THE MARGIN.** Net is +0.0000 at the
  live mark; it blocks for any read ≥ $0.755. Sourced from the position scanner (staler than
  the fresh new-short quote). This is what flips the knife-edge to a block.
- **(c) new_short.bid stale/zero — NO.** Bid is a healthy live **$0.75**. Disproves the prior
  opening-rotation theory.
- **(d) Re-analyze uses a different/buggier gate — NO.** Same gate as scan (§1).
- **(e) units/multiplier/sign error — NO.** Per-share basis and sign are correct
  (re-verified; `estimate_roll_from_quotes` matches legs by identity).

**Amplifier (point 4, confirmed):** the buyback is now a **real-premium** short (~$0.75), so
the gate's **sell-new-at-BID vs buy-old-at-MARK** basis has real teeth — the new short's
bid-haircut (mark 1.02 → bid 0.91 on the target $24; mark 0.785 → bid 0.75 on the forced $25)
is subtracted while the buyback gets no haircut. Against a near-worthless short (this morning)
the haircut was harmless; against a $0.75 short it erases the whole credit.

**The credit is genuinely available** — engine's own `mark_net` for the forced $25 is **+$0.035**;
and a nearer manual strike ($24 8/21 mid $1.02 − buyback $0.75) is **+$0.27**. The gate
manufactures the debit via *spread-gate strike substitution* + *bid-vs-mark, mismatched-timestamp basis*.

## 6. Recommended fix (report only — no code changed)

Keep the "rolls for credit" rule; fix the two things that manufacture the false debit:

1. **Evaluate the credit gate on a same-timestamp, marketable/mid basis** — `mark_net`
   (both legs from one fresh quote) with a `give_up` haircut — instead of
   `fresh new.bid − stale scan-snapshot old.mark`. This flips the live case to a clear
   +$0.035 credit and removes the temporal-source asymmetry. A real debit (mid-to-mid < 0)
   still blocks. RH also returns `high_fill_rate_sell_price` (what you'd actually collect,
   here $0.766 on the $25) — a better sell estimate than raw bid.
2. **Stop the spread gate from PUSHING the strike far off target.** A flat 10% bid/ask gate
   is too tight for mid-vol names (RIOT OTM weeklies are normally 11–30%); it rejects the
   whole on-target δ chain and forces a far-OTM low-bid strike that then fails the credit
   gate. Options: relativize the spread gate (absolute-cents cap, or looser % for
   sub-$1 options), OR — if all on-target-δ strikes are spread-rejected — **defer/WATCH with
   a distinct reason** ("chain too wide to roll for credit right now") rather than substitute
   a far-OTM strike and then report a misleading "net debit."

## 7. Confirmation + follow-ups for the operator
- **Nothing placed. No consent/verdict write. No prod contact that mutates state.** Read-only
  MCP quotes + local computation only.
- To close the ±cent and confirm the selected strike, pull two prod values (I can't from here):
  (a) the RIOT `PMCCPosition.short_leg_mark` the engine currently holds, and
  (b) the `pmcc_roll_aborted` audit payload (`open_bid`, `close_mark`, selected strike) —
  expect selected = **$25.0 8/21**, `close_mark` ≈ the $23.5 8/14 scan mark.
- Current short taken as **$23.5 C 8/14** per your statement + corroborated by the
  abort-reason discriminator (§3). I did not query RH positions (account not specified /
  tool guardrail); say the word + give the account and I'll confirm read-only.
- **STOP for review.**
