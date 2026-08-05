# TASK 3 — Kalshi fee-schedule verification (2026-08-05, evidence-only, read-only)

Verifying the fee the kalshi_crypto_v2 lab harness charged against Kalshi's actual published schedule,
for the four `KX{BTC,ETH,SOL,XRP}15M` up/down series and the hourly ladders
(`KXBTC/KXETH/KXSOLE/KXXRP`). **Read-only research; touches no live config, no D5 ruling, no BACKLOG
closure.**

## 1. What the harness charged (code-verified)

`research/kalshi_crypto_v2/lab/ev.py` sets the fee on **every** fill — taker *and* maker — via the
canonical `_sports_math.kalshi_fee`:

```python
# lab/ev.py:38-39   (maker leg AND taker leg both go through _bet_ev → this)
leg = LegFill(venue="kalshi", side=side, qty=qty, price_per_unit=price,
              fee=kalshi_fee(qty, price))
```
```python
# trading_corp/agents/strategies/_sports_math.py:30-39
def kalshi_fee(contracts, price):
    raw = 0.07 * contracts * price * (1.0 - price)
    return math.ceil(raw * 100.0) / 100.0          # round UP to next cent
```
Docstring citation in that file: *"Kalshi taker fee is `f = ceil(0.07 × C × P × (1−P) × 100)/100`
per fill (Kalshi fee schedule, https://kalshi.com/docs/fees). Round direction is UP."*

**So the harness applied the TAKER fee formula to MAKER fills too** — makers were charged the full
taker fee.

## 2. Kalshi's actual published schedule

The official page (`kalshi.com/docs/fees`) and the July-2026 schedule PDF (`kalshi-fee-schedule.pdf`)
are **bot-blocked** to automated fetchers (HTTP 429 on WebFetch; a curl returns the JS bot-gate HTML,
not the PDF). Verified instead from two independent detailed third-party reproductions that quote the
schedule, which agree with each other and with the harness's taker formula:

| item | value | source |
|---|---|---|
| **Taker fee** | `ceil(0.07 · C · P · (1−P) · 100)/100` per contract, round **up** to the cent | pm.wiki, marketmath.io (both quote `0.07 × P × (1−P)`); **matches the harness exactly** |
| **Maker fee** | **25% of the taker fee** = `ceil(0.0175 · C · P · (1−P) · 100)/100` | marketmath.io: *"Maker fee: 1.75% × p × (1−p) per contract (maker)"* / *"1/4 of taker rate"*; pm.wiki: *"Maker fees are exactly 25% of the taker fee."* |
| **Series scope** | **Uniform** 0.07 taker / 0.0175 maker; **no** crypto- or 15m/hourly-specific multiplier | pm.wiki + marketmath.io both: *"no category/series-specific multipliers"* |
| **Rounding** | ceiling to the next cent, **per contract** | marketmath.io: *"ceil(0.07 * P * (1-P) * 100)/100 … rounds up to the nearest cent"* |

**Verdict on TASK 3.1(a):** the **taker** formula in the harness is CORRECT — `roundup(0.07·C·P·(1−P))`,
confirmed against both the code and two schedule reproductions.

**Verdict on TASK 3.1(b):** these series do **NOT** charge makers zero, and they do **NOT** charge
makers the full taker fee. Makers pay **25% of the taker fee**. The harness charged makers the **full
taker fee → a ~4× over-charge on every maker fill.**

### Residual uncertainties (flagged for operator confirmation from the official PDF)
1. **Crypto multiplier.** One aggregate search summary claimed crypto markets carry a *higher* multiplier
   than 0.07; **neither detailed source corroborates it** (both say uniform 0.07), and the harness +
   its official citation use 0.07. Treated as uniform 0.07 here. If the official PDF shows a crypto
   multiplier > 0.07, the **taker** leg was under-charged — but taker-at-open is already ruled DEAD
   ground (D5), so this would not reopen anything; it would only matter for a future taker strategy.
2. **Maker exact %.** 25%-of-taker is corroborated by two sources but not read off the official PDF
   (bot-blocked). The re-score below therefore **brackets** the maker fee at {100% (old), 25% (likely
   actual), 0% (if these series are in fact maker-free)} so the operator can rule regardless of the
   exact official figure. Empirical API cross-check (real KAREN fills' fee fields) status: see §4.

## 3. Re-score under the corrected maker fee (TASK 3.2)

Driver `_kcv2_maker_fee_rescore.py` (in `harness/`) re-runs the two maker studies changing **only** the
maker fee, caching the fee-independent holdout/candles per asset, using the **same per-attempt mean/SE
`t=mean/se`** the originals use (these two studies carry **no** window clustering — the `t_clus` CR0
clustering was a *different* study, mid-window calibration). Fee model = `ceil(mult·C·P·(1−P)·100)/100`
at qty=1. **Parity gate PASSED:** the `OLD 0.07` column reproduces the 2026-08-02 originals exactly
(ETH maker-resolution 1b-ALL = +0.0300 t+2.5; ETH realism gate m+0 = −0.0022 t−0.2).

**A. Maker-resolution — per-attempt EV, variant A (model side), most-pessimistic `1b ALL-combined`:**

| asset | OLD 0.07 (harness) | maker 0.0175 **= 0.035** | maker 0.0 (free) |
|---|---|---|---|
| ETH | +0.0300 (t2.5) | **+0.0381 (t3.1)** | +0.0472 (t3.9) |
| BTC | +0.0081 (t0.7) | +0.0163 (t1.3) | +0.0255 (t2.1) |
| SOL | −0.0176 (t−1.4) | −0.0093 (t−0.8) | −0.0003 (t−0.0) |
| XRP | −0.0157 (t−1.3) | −0.0076 (t−0.6) | +0.0014 (t0.1) |

**B. ETH realism/latency GATE (`a+b+c` realism+full-pessimism) — the maker-shadow arbiter:**

| placement | OLD 0.07 | maker 0.0175 **= 0.035** | maker 0.0 (free) |
|---|---|---|---|
| m+0 | −0.0022 (t−0.2) | **+0.0057 (t+0.5)** | +0.0139 (t+1.2) |
| m+1 | −0.0014 (t−0.1) | +0.0059 (t+0.5) | +0.0141 (t+1.2) |
| m+2 | −0.0148 (t−1.3) | −0.0080 (t−0.7) | +0.0000 (t+0.0) |
| BTC/SOL/XRP (controls) | negative all delays | negative all delays | negative all delays |

**Readings (evidence, not verdict):**
1. **The `0.0175` and `0.035` columns are byte-identical.** At qty=1 the per-contract ceil-roundup
   floors the maker fee at **1¢** whether the crypto taker multiplier is 0.07 or 0.14 (both →
   `ceil(≤0.875)/100 = $0.01`). ⇒ **the unresolved crypto multiplier (§2) is MOOT at executable size**
   — the correct maker fee is 1¢/contract for any crypto multiplier ≲ 0.28. This retires the §2
   residual-uncertainty #1 for practical purposes.
2. **The correction is real but small:** a uniform **~+0.008/attempt** lift (harness charged full-taker
   ~1–2¢; correct maker ~1¢). Directionally favorable to every maker cell.
3. **No D5 conclusion reopens.** The ETH realism/latency gate — the load-bearing maker-shadow gate that
   killed the ETH survivor at D5 — moves from −0.002 to **+0.006 at m+0 but stays within noise (t+0.5)**
   and **still decays negative by m+2**. Even at a **zero** maker fee it is only +0.014 (t+1.2), decaying
   to 0.000 by m+2. It does **not** cross the "positive AND |t|≥2 across delays" bar under any fee
   assumption. BTC/SOL/XRP stay negative throughout.
4. The pre-realism maker-resolution ETH cell improves +0.030→+0.038 (t3.1), but that study was
   superseded at D5 by the realism gate (which removes its same-minute-close look-ahead), so it is not
   decision-relevant on its own.

**Net:** the harness DID over-charge makers, and correcting it lifts the maker EVs by ~+0.008/attempt,
but nothing crosses the survival/significance bar — the D5 "maker-shadow PARKED" and "15m direction
CLOSED" conclusions stand on the corrected fee. Whether the ETH m+0 −0.002→+0.006 shift (still t+0.5)
merits any note is the operator's call.

## 4. Empirical API cross-check status

The operator's alternate source — real KAREN fill fee fields via the Kalshi REST API — is **not
available**: the reusable authenticated client exists (`_kalshi_auth.KalshiRest(prefix="KALSHI_KAREN")`,
RSA-PSS signed, creds from KV `KALSHI-KAREN-*` via `DefaultAzureCredential`) and *could* call
`/portfolio/fills`, but the kalshi_crypto_v2 work is **entirely read-only census + candle pulls with no
order surface** — KAREN has **never traded** the KX*15M / hourly crypto series, so there are **no fills
and thus no fee fields** to inspect. The schedule facts here rest on the code + two independent
third-party reproductions; **the exact crypto taker multiplier should be confirmed from the official
PDF** (`kalshi.com/docs/kalshi-fee-schedule.pdf`, bot-blocked to agents) — though §3.1 shows it does not
change the qty=1 answer.

## 5. Rounding treatment (TASK 3.3) — premise corrected

**The harness EVs are NOT formula-exact / volume-scale — they are per-contract (qty=1) rounded.** Both
`realized()` and `model_ev()` call `kalshi_fee(1.0, price)` (`ev_forensic.py:168,175`), so the
`ceil(...·100)/100` roundup **bites at 1-contract scale on every fill** (no amortization). The re-score's
identical 0.0175/0.035 columns are the direct evidence: the maker fee's sub-cent formula value
(`0.0175·p(1-p)` ≈ 0.16–0.44¢) is rounded **up to a full 1¢** for essentially all prices at qty=1.

Implications for sizing math:
- The harness already books the **maximum small-size (1-contract) fee** — it is the *worst case*, not
  the volume-scale case the premise assumed. So the studies are, if anything, conservative on fee.
- **The per-contract round-up is a genuine small-size penalty, and it is LARGER for makers**: it turns a
  ~0.4¢ formula maker fee into 1¢ (a ~0.6¢/contract penalty), versus ~0.25–0.4¢ on the taker rate.
- At larger fill size N, `ceil(mult·N·p(1-p)·100)/(100·N)` → the formula value, so the effective
  per-contract maker fee **falls toward ~0.4¢** as size grows. Future sizing should model fee as
  `ceil(mult·N·p(1-p)·100)/100` per *fill* (not per contract × N) to capture this amortization; at
  meaningful size the maker fee is ~4× cheaper than the harness's qty=1 taker charge.

## 6. Bottom line for the operator's ruling

| # | question | finding |
|---|---|---|
| 3.1a | taker = `roundup(0.07·C·P·(1−P))`? | **YES** — matches the code exactly + two schedule sources. |
| 3.1b | do these series charge makers zero / on a maker-fee list? | **Makers pay 25% of the taker fee** (not zero, not full-taker). The harness charged makers the **full taker fee** → over-charge. Official crypto multiplier unconfirmed (PDF bot-blocked) but **moot at qty=1**. |
| 3.2 | does the correct maker fee reopen anything? | **No.** ~+0.008/attempt lift; ETH realism gate −0.002→+0.006 (t+0.5), decays negative by m+2, fails even at zero fee. D5 conclusions stand. |
| 3.3 | rounding treatment | Harness EVs are **qty=1 per-contract-rounded (worst-case)**, not volume-scale-exact — premise inverted. Per-contract round-up is a ~0.6¢ small-size penalty on makers that amortizes away at size. |

**Nothing here touches the D5 ruling, the BACKLOG closures, or any live config** — evidence only.
Recommend recording the maker-fee correction (harness over-charged makers; corrected numbers above) as
a documented lab-harness fix for any future maker work, and confirming the crypto taker multiplier from
the official PDF at leisure (does not change the qty=1 conclusion).
