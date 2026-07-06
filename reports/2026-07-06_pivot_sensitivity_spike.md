# Pivot-degree sensitivity spike — 2026-07-06

Read-only research spike. GROSS only, k=1 causal, nothing pushed, no prod writes.
Detector = certified `SfpModeBDetector`, byte-identical (md5 `91fd76726364331c8083aaaa68fce199`).
One variable: `pivot_len ∈ {5,10,20,50}` (pre-registered, no exploration).

> ⚠ **PROVENANCE CAVEAT (Part 2 only):** the 230d statistical run uses **Bybit** public
> 15m klines, **not Bitunix**. 15m major-swing structure is nearly identical across major
> venues, so the *pivot-degree sensitivity* conclusion transfers — but a **live-deploy
> decision would require re-validation on Bitunix data**. This spike answers "does pivot
> degree matter, in principle," NOT "which pivot to deploy on Bitunix."
> Part 1 (below) uses **prod Bitunix 3m→15m** — the real venue — so it is exact.

---

## PART 1 — Bucket-B direct categorical check (prod Bitunix data)

For each of the 3 forensic Bucket-B setups, at each `pivot_len`, drive the certified
detector (LONG + SHORT via M2=0 reflection) and report sweep/reclaim/BOS with numbers.
Data: prod Bitunix 3m (06-18→07-06) resampled to 15m. Setup bar anchored to the operator's ts.

### Result: NONE fire at ANY pivot_len {5,10,20,50}

**BTC 07-02 13:15** — 15m bar `O61653.7 H61954.7 L61631.2 C61668.2`
- LONG: nearest pivot-low is BELOW price at every degree (pl5=61100 … pl50=57773); setup
  `L=61631` never reaches it → **no sweep** at any degree.
- SHORT: setup `H=61954` is above the pivot-high (pl5=61430 … pl50=60758) but `C=61668`
  closed **ABOVE** it (permit already False) → **reclaim fails** (a break-through, not a failed sweep).
- **A continuation bar that closed through local resistance — not an SFP at any degree.**

**SOL 06-29 17:15** — 15m bar `O75.25 H76.43 L75.23 C75.9`
- LONG: pivot-lows far below (pl5=72.29 … pl50=69.68); `L=75.23` never reaches → **no sweep**.
- SHORT: `H=76.43` above the pivot-high (pl5=74.39 … pl50=72.38) but `C=75.9` closed **ABOVE**
  it → **reclaim fails**. The bar is **making a new high** (the start of a fresh pivot), not fading one.

**SOL 06-28 19:45** — 15m bar `O70.76 H70.76 L70.21 C70.69`
- LONG pl5: pivot-low=70.92, `L=70.21` sweeps it BUT `C=70.69` closed **below** 70.92 →
  **reclaim fails** (a break, not a reclaim; permit disarmed).
- LONG pl10/20/50: pivot-low=70.11, `L=70.21` **misses by 0.10** → **no sweep**.
- SHORT: pivot-highs 72.16–73.15, `H=70.76` far below → **no sweep**.
- (The 70.11 pivot WAS swept+reclaimed 3h later at 22:45 — which the detector DID fire, per the forensic.)

### Verdict (Part 1)

**A smaller pivot_len does NOT catch the operator's Bucket-B swings.** At every degree tested
the setup bar is either (a) a continuation that closed **through** the nearest swing (BTC 07-02,
SOL 06-29) or (b) a near-miss / break-not-reclaim (SOL 06-28). These reads are **not
sweep+reclaim SFP-shaped at any pivot degree** — so the Bucket-B gap is not (only) "pivot too
large"; it is that these specific chart reads do not match the SFP template the detector implements.
(ts are operator-approximate; result is exact at the anchored bars.)
