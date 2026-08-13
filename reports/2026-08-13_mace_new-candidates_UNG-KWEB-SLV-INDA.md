# MACE new-candidate deep chain analysis: UNG, KWEB, SLV, INDA — 2026-08-13 (read-only)

Deep chain-level vetting of 4 price-screened candidates for the $4k MACE account. No config changed.
E=$3,840, rung_risk_pct 0.055 (~$211 budget), max_contracts 1, credit floor 0.30*width, risk_band_max 250,
20d shorts, expiry 2026-09-25 (43 DTE) where listed.

**DATA CAVEAT:** market CLOSED (analysis ~17:40 ET). Strike-grid geometry, open-interest, and today's volume
are reliable (daily aggregates); deltas/marks/bid-ask are POST-CLOSE approximate (OTM bids -> 0). The
STRUCTURAL blockers below (dead wings by OI, $5 grid gaps, low IV, tiny-credit-at-low-price) are robust and
independent of the post-close degradation; the marginal credit-floor pass/fail figures are approximate.

## Ranked table (all four vs the incumbent picks IBIT/FXI)

| Rank | Sym | Spot | ATM IV | Min viable W | est max_risk | credit floor | 1-ctr @$4k | Liquidity | Ex-div boot-gate | Diversification axis | Verdict |
|------|-----|------|--------|--------------|-------------|--------------|-----------|-----------|------------------|----------------------|---------|
| ref | IBIT | $36 | ~35% | 1 | $68 | PASS | YES | good (A/B) | none | **crypto (orthogonal)** | incumbent #1 |
| ref | FXI | $35 | ~21% | 1 | $68.5 | PASS (barely) | YES | thin (C) | YES (Dec) | China EM | incumbent #2 |
| — | **UNG** | $9.97 | ~35% | 0.5 (wings die at W1+) | $38.5 | **FAIL** ($0.115<$0.15) | **NO** | **D** (wings dead) | none | **nat gas — TRULY orthogonal to SPY/crypto/gold** | NOT tradeable |
| — | **KWEB** | $26.84 | ~30% | asym 1/1.5 (wings thin) | $114.5 | **FAIL** ($0.355<$0.45) | **NO** | short legs A/B, **wings C/D** | YES (Dec, =FXI) | China internet (= FXI axis, redundant) | NOT tradeable |
| — | **INDA** | $50.00 | ~15% | 1 | $65 | pass marginal | **NO** | **D** ($53C wing OI 129) | YES (Dec) | India EM (distinct) | NOT tradeable |
| — | **SLV** | $58.16 | ~41% | 5 (call $65->$70 gap) | ~$355 | **FAIL** | **NO** | thin shorts | none | silver (orthogonal-ish) | NOT tradeable |

## Per-symbol detail

### UNG (nat gas, $9.97) — the diversification IDEAL, but untradeable
- Grid CLEAN ($0.50 through the money) — geometry is fine. 20d put $9.00 (d-0.182), 20d call $11.00 (d0.237).
- **Fails the floor at every width:** best case W=0.5 total credit ~$0.115 < $0.15 floor. Wings at W>=1 are
  DEAD strikes (OI=0, bid=0) — untradeable.
- **The $10-underlying kill (operator's check a):** absolute credit is ~$11.50/contract. RH is
  commission-free (no commission drag), BUT the post-close bid/ask slippage on a 4-leg condor (~$0.205 est)
  EXCEEDS the $0.115 credit by ~78%. Even at half that, slippage consumes the entire edge. Tiny credit at a
  $10 underlying cannot survive 4-leg slippage.
- Gap risk (check c): UNG gaps violently on storage/weather, but defined-risk BOUNDS max loss to ~$38.50/ctr
  — that's actually UNG's best property, NOT the killer. The credit/slippage is the killer.
- Distribution (check d): commodity-pool ETF, no ex-div boot-gate. NONE.
- Axis: nat gas (weather/storage/LNG) — the ONLY candidate orthogonal to SPY, crypto, AND gold. A perfect
  new axis on paper, wasted by an untradeable chain.
- Verdict: NOT tradeable at $10. Revisit only if the account grows enough to run wider wings with real OI,
  or via a higher-priced/deeper-liquidity nat-gas proxy (none obvious). The axis is ideal; the vehicle isn't.

### KWEB (China internet, $26.84) — great short legs, dead wings, redundant with FXI
- Grid CLEAN ($0.50). 20d put $25 (d-0.204, OI **21,829**), 20d call $30 (d0.198, OI **9,570**) — SHORT legs
  are grade A/B, genuinely excellent.
- BUT the WINGS are dead: $24P OI=1, $31.5C OI=25 (bid $0.02), $23P/$32C bid=0. Forces an asymmetric
  put-W1 / call-W1.5 structure -> total credit ~$0.355 < $0.45 floor -> FAIL.
- Distribution: annual (~December) -> SAME ex-div boot-gate as FXI if exdiv_guard:true.
- **KWEB vs FXI head-to-head:** KWEB higher IV (~30% vs 21%) and FAR better short-leg liquidity, BUT worse
  credit-to-width (23.7% vs FXI 31.5%) because wing illiquidity forces the wider asymmetric structure, and
  KWEB FAILS the floor where FXI (barely) passes at a clean W1-symmetric. **FXI is the better single China
  representative for the engine.** And KWEB is the SAME China axis — running both is redundant, not diversifying.
- Verdict: NOT tradeable as-is (wing illiquidity). Recheck intraday (short-leg OI is superb; wings may firm),
  but even then it duplicates FXI's axis. Pick FXI, not KWEB.

### INDA (India, $50) — genuine axis, killed by thin/sparse options
- Expiry: **2026-09-25 NOT listed** — only 2026-09-18 (36 DTE); monthly-only, no weeklies (sparse).
- Grid: $1 only (no $0.50). 20d put $48 (d-0.234), 20d call $52 (d0.228). W1 credit ~$0.35 >= $0.30 floor
  (marginal, post-close).
- **Liquidity kills it:** the $53 call (W1 call wing) OI=129 = grade D -> a 4-leg condor is NOT fillable.
  The $52 call's OI=20,777/vol=20,013 is a block/roll anomaly, not clean condor liquidity. Put side grade C
  with wide spreads. ATM IV ~15% (low, TLT/XLF-class) -> thin credit.
- Distribution: annual (~Dec); no ex-div before the Sep-18 expiry -> low near-term boot-gate, but sourcing
  needed to enable.
- Axis: India (domestic consumption, INR) — genuinely distinct EM, low SPY/crypto/gold correlation. GOOD
  axis IF liquid — it isn't.
- Verdict: NOT tradeable — liquidity + low IV + sparse expirations.

### SLV (silver, $58) — GLD-style geometry failure (from the prior search, reconfirmed)
- Call grid gaps $65 -> $70 ($5), so the 20d call ($65) forces W=5; call credit $0.615 < $1.50 floor; put
  side can't match at symmetric width. Same coarse-grid failure as GLD/USO at a higher-priced name.
- Axis: silver (~uncorrelated to equities) — good axis, but geometry makes it untradeable at $4k, like GLD.
- Verdict: NOT tradeable.

## Framed recommendation
**Of these four, add NONE.** Framed against the two live questions:

1. **A better SPY-partner than IBIT?** NO. IBIT (crypto, W1, clears floor, deep liquidity, gap bounded to
   ~$68 by defined risk) beats all four. UNG is the only candidate with comparably-orthogonal diversification,
   but its $10 price makes the condor credit too small to survive 4-leg slippage — untradeable. IBIT stays #1.
2. **A better 3rd-symbol-once-OQ-2-exists than FXI?** NO. FXI (China, clean W1, barely clears floor) beats
   KWEB head-to-head (same axis, redundant, fails floor as-is), and beats INDA/SLV/UNG (all untradeable at
   $4k). FXI stays the eventual #3.

**The honest structural finding:** after IBIT (crypto) + FXI (China-EM), there is NO cheap, liquid,
truly-orthogonal 3rd macro axis reachable at $4k. UNG (nat gas) is the ideal missing axis but is untradeable
at $10; SLV (silver) is untradeable by geometry; INDA (India) by liquidity; KWEB duplicates FXI. **The account
is diversification-constrained by SIZE, not by symbol selection.** The realistic $4k ceiling is SPY (equity)
+ IBIT (crypto). Everything beyond that waits on either more equity or the OQ-2 entry-window work — and even
then the best available #3 (FXI) is still EM-equity, only partially decorrelated from SPY.

If a genuinely new axis is a priority later, the two worth re-vetting on LIVE quotes (not post-close) are
KWEB (superb short-leg liquidity; needs wings to firm + accept China-overlap) and UNG (ideal axis; needs the
account large enough to run wider wings with real OI). Neither clears today.
