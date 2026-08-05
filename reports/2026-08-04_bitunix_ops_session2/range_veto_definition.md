# TASK 3 — "up_but_bearish RANGE-only veto": definition + backtest (2026-08-05)

**Backtest-only; no live-logic change (per ruling — any live veto deferred to the n≥30 review).**
Harness `_sfp_range_veto.py` (same current-construct entries, parity path; run capped). GROSS + net
of the SFP fee model; 4.00 yr, in-sample Binance proxy. Evidence, not closure.

## The term, operationalized

The construct is bidirectional; a SHORT is "up_but_bearish" when the higher context is UP but the
setup is bearish. Two computable definitions from the **existing** regime classifiers:

- **DEF A (rd-only):** up_but_bearish short := a short whose **1h LuxAlgo range-detector break-state
  `rd_os == +1`** (a confirmed up-break / uptrend). The RANGE-only veto drops these; it keeps shorts
  only when `rd_os ∈ {0 range, −1 downtrend}`. Single classifier, self-consistent with "RANGE-only."
- **DEF B (macro × rd):** up_but_bearish short := `macro60 == "bull" AND rd_os != 0` (slow macro up
  and not a confirmed range = counter-trend short into a bull macro); keep bull-macro shorts only
  when `rd_os == 0`.

## Decisive composition finding (why B is empty)

Short book: **n=341, net avgR +0.044** (weaker than the long book, n=286 net +0.135; whole book
+0.085). Classifier composition of the 341 shorts:

| classifier | distribution |
|---|---|
| `rd_os` (1h) | −1 (downtrend) **195** · 0 (range) **98** · +1 (up-break) **48** |
| `macro60` | **bear 341** · bull 0 · neutral 0 |
| `reg15` (15m) | down 273 · range 68 · **up 0** |

**Every short is already `macro60==bear` and none is `reg15==up`.** The construct's with-trend entry
gate structurally excludes bull-macro / up-regime shorts — so **DEF B vetoes n=0 (vacuous)**, as does
any ema200/macro "up-context" variant. **The only up-context short that actually occurs is the
range-detector up-break (`rd_os==+1`, n=48)** — DEF A. So DEF A is the *only non-empty*
operationalization of "up_but_bearish" on this construct.

⚠ **Construct-delta caveat (per scope condition #2).** This harness uses the research construct's
**macro60** with-trend gate (parity 627/+0.182), not the live **per-coin** `trend_mode` (BTC
ps_trail30 / ETH·XRP rd / SOL ema200). The per-coin live gate *could* admit a bull-macro short (e.g.
BTC ps_trail30==bear while macro60==bull), so DEF B's emptiness is a property of this harness's gate,
not proven for the live per-coin construct. **DEF B is untestable here and needs the per-coin-gated
construct — a follow-up, not a closed negative.**

## DEF A backtest (veto rd_os==+1, n=48 removed = 14% of shorts)

| | net avgR | note |
|---|---|---|
| vetoed subset (n=48) | **−0.088** | a net-losing subset (removing it adds +4.2R) |
| kept shorts (n=293) | +0.066 | vs +0.044 baseline |
| **short book** | +0.044 → **+0.066** (Δ +0.022) | |
| **whole book** (longs+shorts) | +0.085 → **+0.100** (Δ +0.014) | longs untouched |

Right sign pooled — but **not robust** on the two cuts that matter:

**Per-coin (the veto is coin-inconsistent):**
| coin | vetoed n | vetoed net avgR | read |
|---|---|---|---|
| SOL | 10 | **−0.296** | vetoing removes losers → helps |
| XRP | 18 | **−0.504** | vetoing removes losers → helps |
| BTC | 11 | **+0.157** | vetoing removes *winners* → hurts |
| ETH | 9 | **+0.679** | vetoing removes *big winners* → hurts |

The pooled lift is entirely **SOL+XRP** (their rd-up-break shorts are losers) net of **BTC+ETH damage**
(their rd-up-break shorts are winners). "Short-into-an-rd-up-break is bad" holds for SOL/XRP, is
**false** for BTC/ETH.

**Holdout (unstable):** IS shorts (n=170) veto Δ **−0.029** (hurts); OOS (n=171) veto Δ **+0.085**
(helps). The benefit is OOS-only; in-sample the veto is slightly negative. No consistent sign across
the split.

## Recommended pin + verdict

**Pin the definition = DEF A:** *"an up_but_bearish short is a short taken while the 1h LuxAlgo
range-detector break-state `rd_os == +1` (confirmed up-break); the RANGE-only veto keeps shorts only
when `rd_os ∈ {0, −1}`."* It is the single-classifier, RANGE-only-consistent, and **only non-empty**
operationalization on this construct — the natural computable meaning of the term.

**But the evidence does NOT justify wiring the veto live** (which the ruling already defers to the
n≥30 review): the whole-book lift is small (+0.014), **coin-inconsistent** (removes ETH's +0.68 and
BTC's +0.16 winners while helping SOL/XRP), and **holdout-inconsistent** (IS −0.029 / OOS +0.085). A
global veto would sacrifice the ETH/BTC counter-trend shorts that are actually winners.

**If revisited after n≥30:** (a) scope the veto **per-coin to SOL/XRP** rather than global (that is
where up-break shorts are losers), and (b) re-test **DEF B on the live per-coin-gated construct**
(untestable here because the macro60 gate makes it empty). Longs are never affected either way.
Evidence only; the operator rules in.
