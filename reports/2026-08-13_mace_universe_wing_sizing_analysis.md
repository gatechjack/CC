# MACE universe-wide wing-geometry + sizing analysis — 2026-08-13 (read-only; no config change)

Question: (1) does the GLD "wing not listable" problem exist for the other symbols, and
(2) what single (rung_risk_pct, risk_band_max) lets MACE trade the tradeable set — GLD@$5 included —
at ~$4k equity without blocking symbols or oversizing rungs.

Data: live Robinhood chains (MCP market-data, account-agnostic), all at expiry **2026-09-25** (43 DTE,
the highest DTE in [30,45] — the same expiry MACE used for SPY/GLD today). E = mace_equity_snapshot
2026-08-13 = **$3,840.45**. Current config: rung_risk_pct 0.055, risk_band_max_usd 250,
risk_band_min 50*width, credit_floor 0.30*width, max_contracts 1, max_rungs 5, deployment_target 0.80.

**CAVEAT — post-close quotes.** All option deltas/mids were pulled ~16:00-16:15 ET (after the cash close),
so far-OTM bids thin to 0 and credit/max_risk are POST-CLOSE MIDS (approximate). Strike-grid geometry
(which strikes are listed) is time-invariant, so min-viable-width / no_wing conclusions are solid; the
credit-floor pass/fails that are "barely" (FXI/IBIT +$0.015; GLD credit 1.635 vs 1.50) should be
re-confirmed on live 15:45 quotes before acting.

## Per-symbol findings

| Sym | Spot | ATM IV | 20d put / call short (real d) | Grid problem? | Min viable W | est credit | max_risk (W-cr)x100 | credit floor 0.30W |
|-----|------|--------|-------------------------------|---------------|--------------|-----------|----------------------|---------------------|
| SPY | 777.77 | ~ | 746P / 802C (today's fill) | none ($1) | **3** | 0.91 | **$209** | 0.90 -> PASS |
| GLD | 398.96 | ~22% | 378P(-0.213) / 430C(0.198) | call $5 grid >418 | **5** | 1.635 | **$336** | 1.50 -> PASS |
| TLT | 82.58 | ~8.3% | 80P(-0.185) / 85C(0.214) | none ($0.50) | 1-2 | 0.41 @W2 | $159 @W2 | 0.60 -> **FAIL** (0.30@W1 also fails 0.26) |
| USO | 125.05 | ~46% | 112P(-0.20) / 150C(0.188) | call $5 grid >141; **ASYMMETRIC** (put ok @1, call needs 5) | **5** | 1.39 | **$361** | 1.50 -> **FAIL** |
| EWZ | 33.78 | ~23% | 32P(-0.22) / no clean 20d call | thin/illiquid OTM | ~1-2 geom | indeterminate | n/a | **FAIL** (illiquid) |
| FXI | 34.88 | ~21% | 33P(-0.20) / 37C(0.224) | none ($0.50) | **1** | 0.315 | **$68.5** | 0.30 -> PASS (by $0.015) |
| IBIT| 35.87 | ~35% | 33P(-0.228) / 40C(0.207) | none ($0.50) | **1** | 0.320 | **$68** | 0.30 -> PASS (by $0.020) |

**Does the GLD problem generalize?** YES for **USO** (call grid $1->$5 above ~$141; 20d call $150 in the
$5 zone; put side would be fine at $1 but the call forces $5 -> ASYMMETRIC, and MACE uses one width).
NO for TLT/EWZ/FXI/IBIT (their grids stay $0.50/$1 through the 20d wing zones). So the "wing not listable"
geometry hits the two HIGH-priced names (GLD, USO). The cheap names have a DIFFERENT problem (credit floor).

## Sizing at E=$3,840, current rung_risk_pct 0.055 (budget = $211)

| Sym | Min viable W | max_risk | credit-floor OK? | 1-contract at $4k @0.055? | min E for 1 ctr | min rung_risk_pct to size 1 at $4k |
|-----|------|---------|------------------|----------------------------|-----------------|-------------------------------------|
| SPY | 3 | $209 | YES | **YES** (211>=209) | $3,800 | 0.0523 (already trades) |
| GLD | 5 | $336 | YES | **NO** (0 ctr) | $6,109 | 0.084 (+ risk_band_max>=340) |
| TLT | 2 | $159 | **NO** (low vol) | blocked by floor | n/a | 0.040 if floor waived (not recommended) |
| USO | 5 | $361 | **NO** + max_risk>250 | blocked | n/a | 0.090 if floor+ceiling waived |
| EWZ | ~1-2 | indeterminate | **NO** (illiquid) + boot-gated | blocked | n/a | n/a |
| FXI | 1 | $68.5 | YES (barely) | **YES** (cap 1) | $1,245 | 0.017 (boot-gated) |
| IBIT| 1 | $68 | YES (barely) | **YES** (cap 1) | $1,236 | 0.017 (overflow-only) |

## The risk question — single rung_risk_pct to include GLD@$5

- To size GLD ($336 max_risk) to 1 contract: rung_risk_pct >= 336 / E. At $4,000 -> **0.084**; at the actual
  E=$3,840 -> **0.0875**. Use **~0.088** for a small margin (budget 0.088*3840 = $338 >= $336). NOTE GLD is
  KNIFE-EDGE at 0.088: if that day's credit is < ~1.64 (max_risk > $338) GLD sizes to 0 again.
- Per-rung risk as % of equity at min-viable width (max_risk / E):
  SPY 5.4% | GLD 8.75% | USO 9.4% | TLT 4.1% | FXI 1.8% | IBIT 1.8%.
- **Does raising rung_risk_pct oversize the cheap names? NO — max_contracts:1 still binds.** At 0.088
  (budget $338), FXI/IBIT ($68) would size to floor(338/68)=4-5 by budget, but max_contracts:1 caps them to
  **1**. Confirmed: with max_contracts:1, rung_risk_pct only decides 0-vs-1 admission; it does NOT stack
  contracts. **The true per-rung risk cap at max_contracts:1 is risk_band_max_usd, not rung_risk_pct.**
- **risk_band_max needed** (= each symbol's min-viable max_risk): SPY 209 (OK under 250), GLD **340**, USO 365
  (moot - floor fails). Current 250 admits SPY only. Admitting GLD needs **risk_band_max >= ~$340** — a
  Board-ratified-parameter change (the 250 came from the width-scaling ruling).
- **Deployment (80%) check.** Cap = 0.80 * $3,840 = **$3,072**. Full 2-symbol x 5-rung stack (SPY 5*209 +
  GLD 5*336) = 1,045 + 1,680 = **$2,725 < $3,072** -> does NOT bind (headroom ~$347, thin). Universe is
  capped at 2 symbols (OQ-2), so a 3rd symbol would blow the cap — but that's gated anyway.

## Honest verdict per symbol
- **SPY — tradeable now** (W3, $209, 1 ctr @0.055). No change needed.
- **GLD — only after rung_risk_pct -> ~0.088 AND risk_band_max -> >=$340** (W5), and even then MARGINAL/knife-edge
  at $4k. Comfortable only past ~$6k equity.
- **TLT — NOT tradeable at $4k regardless of width/equity** — credit floor fails (ATM IV ~8%; a 20d condor
  collects < 0.30*width). Fixable only by a higher-vol regime, not by config.
- **USO — NOT tradeable at $4k regardless** — asymmetric geometry forces W5 (call $5-grid), max_risk $361
  exceeds any sane ceiling AND credit floor fails. Benched.
- **EWZ — NOT tradeable** — thin OTM option liquidity (credits indeterminate on real bids) + boot-gated
  (no ex-div date). Re-confirm on live quotes only if ever seriously considered.
- **FXI — tradeable at W1** ($68.5, floor passes by $0.015) — but boot-gated (no ex-div date) and razor-thin
  floor margin (live slippage could flip it). No rung change needed.
- **IBIT — tradeable at W1** ($68) — but overflow-only by design (never a primary). Fine as an overflow receiver.

## Recommended (rung_risk_pct, risk_band_max) with consequences
**Answer to the literal question:** the smallest pair that trades the widest set INCLUDING GLD@W5 at $4k is
**rung_risk_pct 0.088 + risk_band_max_usd 340**. It admits SPY + GLD (GLD marginally); FXI/IBIT are already
W1-viable (gated); TLT/USO/EWZ are NOT fixable by these two knobs (credit floor / geometry).

**My recommendation: do NOT adopt that pair at $4k — keep 0.055 / 250 and leave GLD disabled.** Reasons:
1. GLD is knife-edge at 0.088/$4k (sizes to 1 only if that day's credit >= ~1.64; a small credit or equity
   dip -> 0 contracts again). You'd spend a Board risk-parameter change (250->340) to admit ONE fragile symbol.
2. At max_contracts:1, raising rung_risk_pct buys nothing except admitting bigger single condors — and the
   real per-rung tail then rises from $250 to $340. It is also a **latent amplifier**: the day max_contracts
   goes above 1, a 0.088 budget lets the cheap names (FXI/IBIT $68) stack multiple contracts and every SPY
   rung risks more per trade. Couple any 0.088 adoption with "max_contracts stays 1 until equity grows."
3. TLT/USO/EWZ don't benefit at all, so the change only serves GLD.

**Cleaner path:** run SPY-only at 0.055/250 now; revisit **(0.088, 340) + GLD@W5** once equity clears
**~$6k**, where GLD sizes with headroom instead of on a knife-edge. FXI/IBIT stay gated (ex-div / overflow).
Operator rules on any change.
