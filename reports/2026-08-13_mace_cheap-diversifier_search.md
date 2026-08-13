# MACE small-account diversifier search + FXI/IBIT enable-paths — 2026-08-13 (read-only)

Two parts: (1) exact enablement path for the already-analyzed cheap symbols FXI & IBIT; (2) a live-chain
search for a BETTER cheap diversifier beyond the original 7. No config changed. E=$3,840, rung_risk_pct
0.055 (~$211 budget), max_contracts 1, credit floor 0.30*width, risk_band_max 250.

**DATA CAVEAT:** market is CLOSED (analysis run ~16:20-17:30 ET). Strike-grid GEOMETRY is time-invariant and
reliable; credit/max_risk/liquidity are POST-CLOSE approximate (far-OTM bids -> 0, deltas degrade). Any
"barely passes floor" verdict (FXI/IBIT ~+$0.015-0.02) MUST be re-confirmed on LIVE 15:45-quality quotes
before trusting the symbol to enter vs skip on credit_floor. Live reconfirmation cannot be done now.

## PART 1 — FXI & IBIT enable-paths (verified against mace/config.py + ex_dividend_calendar.yaml)

### FXI — ex-div boot-gate (config.py:396-415)
- The gate: for EVERY symbol with `enabled=true AND exdiv_guard=true`, `config/ex_dividend_calendar.yaml`
  must contain >=1 entry for that symbol, else `load_mace_config` RAISES and the engine won't boot. FXI is
  `enabled:false, exdiv_guard:true` and STRUCTURED-EMPTY (0 ex-div entries) -> enabling as-is = boot fail.
- **Does FXI actually distribute?** YES — iShares China Large-Cap pays semi-annual distributions (~June +
  December, variable, declared only weeks ahead). So exdiv_guard:true is APPROPRIATE (unlike GLD/USO/IBIT
  which pay nothing and correctly set guard:false). Setting FXI guard:false would remove real protection —
  contradicts the Board's own design intent (yaml preamble + the gate's rationale).
- **Timing:** June already passed; the next FXI ex-div is ~**December 2026**. A 30-45 DTE condor entered now
  expires late-Sep — NO ex-div in the window. The guard is moot until condors are held into the Nov/Dec zone.
- **Mechanism (concrete):** ex-div dates live in `config/ex_dividend_calendar.yaml` (hand-maintained, schema
  symbol/ex_date/pay_date/confirmed/source). This is NOT the mace_calendar_cli path — that CLI manages the
  `economic_event` macro-calendar (FOMC/CPI/etc.), a different table. Steps:
  1. Source FXI's 2026 distribution ex-date from iShares (ishares.com FXI fund page -> Distributions).
     December not yet declared this far out -> add a PROJECTED date (confirmed:false, source "projected -
     iShares semi-annual Dec pattern"); refine to confirmed when iShares declares.
  2. Add >=1 FXI entry to ex_dividend_calendar.yaml (satisfies the boot gate; a Dec date is harmless for
     near-term Sep/Oct condors).
  3. Set `symbols.FXI.enabled:true` in mace.yaml + restart (frozen config).
- **★ FLAG — width trap (verify before enabling):** FXI is only floor-viable at **width 1** (universe
  analysis: W2 credit ~$0.42 < $0.60 floor; W1 ~$0.315 >= $0.30, barely). FXI config is `width_dollars:2,
  fallback_width_dollars:1`. Per the plan, `fallback_width` fires ONLY on the wing-listing (`no_wing`)
  check — NOT on credit-floor failure. FXI's $0.50 grid means W2 wings ARE listed -> no no_wing -> fallback
  NEVER triggers -> FXI builds W2 -> skips on credit_floor, never reaching W1. If that read of the fallback
  trigger is correct, **FXI needs `width_dollars` changed 2->1** (make W1 primary), not just enabled.
  VERIFY the fallback trigger condition in strategy.py before acting.

### IBIT — overflow_only -> primary (config.py:383-394)
- IBIT config: `enabled:false, overflow_only:true`. The boot gate: if a symbol is in `universe` AND
  `overflow_only:true`, `load_mace_config` RAISES: "overflow_only and can NEVER be a primary (OQ-3 ratified
  — remove it from universe)". So you cannot add IBIT to universe while overflow_only:true.
- **To promote to primary:** set `symbols.IBIT.overflow_only:false` + add IBIT to `universe` + `enabled:true`
  + restart. Three config edits — BUT flipping overflow_only:false **REVERSES a ratified Board ruling (OQ-3)**;
  the code error even cites it. Per MACE discipline, re-opening a ruling needs a **Board memo**, not a quiet
  toggle.
- **Tradeoff of promoting IBIT:**
  - PRO: it is the BEST diversifier in the universe — spot-BTC, genuinely low correlation to SPY equity beta;
    high IV (~35%) clears the credit floor at W1; cheap ($36, fine $0.50 grid); GOOD option liquidity
    ($40C OI 2178 / vol 5710). It is the one symbol that is both tradeable at $4k AND meaningfully diversifying.
  - CON (why OQ-3 benched it): IBIT tracks Bitcoin — large weekend/overnight gap risk (BTC trades 24/7, the
    options don't; the manage loop can't intervene over a weekend). Defined-risk BOUNDS a full gap-through to
    the width: at W1 that's only ~$68/contract max loss — small. Promoting makes IBIT enter on its own IVR
    merit every eval (subject to weekly budget), raising crypto-exposure FREQUENCY vs opportunistic overflow.

## PART 2 — cheap-diversifier search (live chains, 2026-09-25 / 43 DTE)

Ranked table (post-close approximate credits; geometry reliable):

| Rank | Sym | Spot | Min W | est max_risk | credit floor (post-close) | 1-ctr @$4k/0.055 | Liquidity | Diversification vs SPY | Blockers |
|------|-----|------|-------|-------------|----------------------------|------------------|-----------|------------------------|----------|
| 1 | **IBIT** | $36 | **1** | **$68** | PASS (~+$0.02, live-unconf) | **YES** | good (A/B) | **HIGH** (crypto/BTC, ~uncorrelated) | config: overflow_only->primary = OQ-3 Board-memo |
| 2 | **FXI** | $35 | **1** | **$68.5** | PASS (~+$0.015, live-unconf) | **YES** | thin (C) | MED-HIGH (China EM) | ex-div source + likely W2->1 + boot-gate |
| - | XLF | $58 | 1 | ~$28 | shaky (IV ~13%) | maybe | C (thin calls) | LOW (~0.85 corr) | low-IV floor risk; poor diversifier |
| - | KRE | $78 | 1-2 | inconclusive | inconclusive (calls unquotable AH) | ? | C/D | MED (regional banks) | post-close unassessable; thin |
| - | GDX | $88 | 1 | ~$50 | FAIL (thin 20d premium) | NO | D at shorts | MED (gold miners) | floor + liquidity |
| - | EEM | $67 | 1 | $75-152 | FAIL (IV ~26%) | NO | D at shorts | MED (broad EM) | credit floor |
| - | SLV | $58 | 5 (call-forced) | ~$355 | FAIL | NO | thin shorts | HIGH (silver) | GLD-style $5 gap $65->$70 + floor + size |
| - | ARKK | $83 | 5 (call-forced) | $337 | PASS@W5 but >$211 | NO | D at shorts | LOW (high-beta growth) | size + geometry + poor diversifier |
| - | XOP | $179 | asym 1/5 | $380+ | FAIL | NO | D (OI~0) | MED (oil&gas) | price + $5 call grid + zero OI |
| - | TQQQ | $77 | asym 2/5 | $350-423 | FAIL | NO | C | ZERO (3x QQQ) | leverage gap + size + correlation |
| - | SQQQ | $36 | 1 | ~$50 | pass-ish | NO | D (OI~1) | anti-corr bet | liquidity + 3x decay/drift |

**The search found NOTHING that beats FXI/IBIT.** The $4k window is narrow: you need LOW price (fine grid +
$1-2 wings) AND mid/high IV (clear 0.30*width floor) AND deep liquidity AND real SPY-decorrelation. The
recurring failures: higher-priced names ($58 SLV, $88 GDX, $145+ XOP) coarsen to $5 grids or run too-big
max_risk; low-IV names (XLF ~13%, EEM ~26%) fail the credit floor (TLT disease); leveraged names
(TQQQ/SQQQ/ARKK) are SPY-correlated and/or gap-dangerous. The already-known cheap pair (FXI, IBIT) remains
best precisely because they are low-priced AND adequately-IV'd.

## Recommendation
**Best cheap diversifiers to add NOW at $4k with ZERO risk-% change: IBIT (primary pick), FXI (secondary).**
Both size to exactly 1 contract at $4k under the current 0.055 (budget $211 vs ~$68 max_risk) — no
rung_risk_pct change, unlike GLD.

- **#1 IBIT — the standout.** It is the only universe symbol that is tradeable at $4k AND a genuine SPY
  diversifier (crypto). Since **GLD is benched at $4k** (can't trade), the clean move that stays within OQ-2's
  2-symbol window ceiling is to **SWAP: disable GLD, promote IBIT to primary** -> active book = SPY + IBIT
  (2 that actually ladder). Requires a Board memo to re-open OQ-3; the defined-risk W1 structure caps the
  crypto tail at ~$68/contract, which makes the OQ-3 caution manageable.
- **#2 FXI — secondary** (China EM diversifier), but more friction: source the Dec ex-div date, likely change
  `width_dollars` 2->1 (verify the fallback trigger), razor-thin floor. Prefer IBIT first.
- **Everything else: reject** — none clears geometry x diversification x liquidity at $4k.

**OQ-2 ceiling flag:** the universe is capped at 2 ACTIVE (laddering) symbols until the entry-window
serialization is built (15:45-15:58 fits ~2 symbols at 5x60s ladders). SPY + one diversifier = at the
ceiling. A THIRD active symbol (SPY + IBIT + FXI) needs the OQ-2 work FIRST. So add ONE now (IBIT), or
swap GLD->IBIT to keep the count at 2.

**HARD GATE before enabling either:** re-confirm the credit floor on LIVE market-hours quotes (post-close
margins of +$0.015-0.02 are too thin to trust). Run `mace_shadow_eval` intraday or watch the next 15:45.
