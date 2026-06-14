# PA-redeem-cap backtest tooling — ENGINE VALIDATION (NOT the §4 verdict)

**Date:** 2026-06-14 · **Session:** operator-supervised · **Branch:**
`bitunix-redeem-cap-backtest-tooling-2026-06-14` (off main `32e7fb4`; unmerged)
**Scope:** BUILD + smoke-validate the engine. **NOT a deploy, NOT a §4 decision, NOT the
redeem-cap verdict.** Per the infra inventory's EXTEND plan
(`reports/2026-06-14_bitunix_backtest_infra_inventory.md`).

> **THIS IS ENGINE VALIDATION ONLY.** A defensible §4 redeem-cap verdict requires a high-vol 3m
> regime (separate data-ingest task) and ideally the real prod alert stream. **The smoke numbers
> below are NOT the verdict** — see the "Do not over-read" caveat.

---

## 1. What was built (EXTEND, not new engine)

Added to `scripts/backtest_bitunix_confluence.py` (reuses its corpus loader + score/PA evaluators +
`build_price_context`; the existing `run_backtest` is untouched — additive only):

- **`run_redeem_cap_backtest(...)`** — the redeem-cap engine. `--redeem-arms` runs three arms
  (`no_redeem`=cap 0 / `cap_1bar`=cap 1 / `current`=cap `REDEEM_CAP_CURRENT`); `--redeem-cap N` runs one.
- **v2 economics (graft):** `build_v2_plan` calls the **real `build_trade_plan`** (3-leg + fee gate)
  with swings / HTF levels / ATR-14 recomputed from the 3m corpus (mirrors
  `observer._build_proposal_v2`); `walk_v2` is the **entry-timing harness bar-walk** (SL-first tie,
  ordered TP fills, BE-after-TP1 / TP1-after-TP2 ratchet); net-of-cost at VIP3 taker 0.09%rt / maker
  0.064%rt. Replaces the single-TP fixed-ATR model for the redeem arms.
- **PA-redeem loop (`_simulate_redeem`):** on a PA-reject, re-evaluate score+PA per subsequent 3m bar
  up to the cap (mirrors `observer.run_pa_redeem_loop`: re-run until PA passes / score decays to SKIP /
  cap exhausted). **Late entry priced at the FIRE bar's close** (not the stale signal price).
- **Decision metric = net-of-cost expectancy per fire** (NEVER fire-rate). Per-fire independent walks.

`REDEEM_CAP_CURRENT = 30` 3m bars (90 min) — covers the entry-timing analysis's observed max
`bars_waited`=25; in prod score-decay usually breaks redeem far sooner. Raise it for a fuller §4 run
if score-decay proves rare on the target corpus.

---

## 2. Validation results (smoke — all PASS)

**Graft fidelity — PASS.** `walk_v2` reproduces the etharness walk reference AND the hand-computed
expected R on 5 scenarios (`fidelity_check.py`): tp1+tp2→tp1-floor +0.750, immediate-SL −1.000,
tp1→BE +0.125, all-3-TP +1.250, open. `ref-match` and `expected-match` both True on every case.

**Import + run — PASS** (after guarding the pre-existing breakage, §3). Module imports; the
full-corpus (2026-03-30→05-16, 22,560 bars) no-redeem arm ran clean, and the three-arm 1-week smoke ran clean.

**Three arms (smoke: 2026-05-09→05-16, 3,360 3m bars, 1,509 synth alerts; bybit_hybrid):**

| arm | first-pass | redeem | dropped | plan-skip | walked | **max_bw** | net-taker/fire | net-maker/fire |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `no_redeem` (cap 0) | 57 | 0 | **829** | 40 | 17 | 0 | −0.182 | −0.062 |
| `cap_1bar` (cap 1) | 53 | 28 | 797 | 62 | 19 | **1** | −0.121 | −0.004 |
| `current` (cap 30) | 50 | 96 | 726 | 126 | 20 | **24** | +0.009 | +0.125 |

Per-arm sanity — all confirmed:
- **`no_redeem` drops the PA-reject cohort** (829 dropped, 0 redeem fires). ✓
- **`cap_1bar` fires-or-abandons at 1 bar** (`max_bw`=1; 28 redeem fires). ✓
- **`current` reproduces multi-bar redeem-late entry** (`max_bw`=24 — matches the entry-timing
  analysis's observed max `bars_waited`=25; 96 redeem fires). ✓
- v2 fee-gate active (40/62/126 plan-skips); redeem fires priced at the fire bar; per-fire net-of-cost.

### Do NOT over-read the smoke numbers (this is why it's not the verdict)
On THIS corpus the net-taker expectancy *rises* across arms (`no_redeem` −0.18 → `current` +0.01) —
the **opposite** direction from the entry-timing analysis's "cap redeem" finding. **That is noise, not
a result:** (a) tiny n (17-20 walked per arm); (b) low-vol **Bybit/TradingView** corpus, not the live
**Bitunix** prod data; (c) **synth** alerts (May-16 alertcondition-gap caveats), not the real prod
alert stream; (d) score-decay rarely fires on synth alerts, so `current` over-walks vs prod. The engine
is validated (arms differentiate correctly, fire-bar pricing, graft fidelity); **the numbers do not
overturn the entry-timing recommendation and are not a §4 input.**

---

## 3. Pre-existing breakage found + worked around (filed BACKLOG P2)

`backtest_bitunix_confluence.py` was **unimportable on main `32e7fb4`** — it imports + calls three
things defined **nowhere in the repo**: `_resample_to_3m/_5m/_15m` (coinbase path),
`bitunix_confluence_gate` (whole module, five_factor arm), `bitunix_price_context.build_gate_inputs`
(five_factor). The existing `test_backtest_bitunix_confluence_five_factor.py` was therefore **already
RED at collection on main** (independent of this work). Likely prod-vs-git drift (the five_factor/coinbase
machinery shipped to prod but its defining code was never committed).

**Worked around per operator decision:** the 3 imports are `try/except`-guarded so the **PA +
bybit_hybrid** path (this engine) imports + runs; the five_factor/coinbase arms stay broken-but-LOUD
(raise `NotImplementedError` if used). The five_factor test stays pre-existing-red (it imports the
missing module directly). **Proper repair is a separate task** (recover the missing modules from prod
or delete the dead paths) — filed BACKLOG P2; touches the shared `backtest_btc_accumulator`.

---

## 4. Path to the §4 verdict (NOT this session)

1. **Ingest a high-vol 3m regime** (re-export Feb-2026 3m to `btc_scalping.db`, or pull a known high-vol
   window from the Bitunix/Bybit API) — the data caveat from the infra inventory. Without it, the
   "≥1 ATR-regime rotation" requirement is unmet at 3m.
2. **Prefer the real prod alert stream** over synth where possible (synth carries the May-16
   alertcondition-gap caveats and rarely triggers score-decay).
3. **Re-assess `REDEEM_CAP_CURRENT`** — if score-decay is rare on the corpus, the cap (not decay) bounds
   `current`; size it to the regime's realistic redeem distribution.
4. Decision metric stays **net-of-cost expectancy per fire, holding other gates fixed** (never fire-rate).
5. Repair the pre-existing five_factor/coinbase breakage (P2) if those arms are needed.

---

## Reproduce
```
# graft fidelity
run_capped.ps1 python fidelity_check.py
# three-arm smoke (run from the worktree; data paths are absolute to the main checkout)
run_capped.ps1 python scripts/backtest_bitunix_confluence.py --bar-source bybit_hybrid \
  --alert-source synth --bybit-db <cc>/data/btc_scalping.db \
  --bitunix-5m-cache <cc>/data/historical_alerts/cache_ohlcv_bitunix_5m_20260330_20260516.json \
  --prod-alerts-cache <cc>/data/historical_alerts/cache_alerts_20260430_20260517.json \
  --start 2026-05-09 --end 2026-05-16 --redeem-arms
```
(The worktree's `data/` is gitignored/empty — point `--bybit-db` etc. at the main checkout's absolute paths.)

## Disclosure (82fda13)
No agent SSH this session (local build + local backtest DBs only). No prod write, no deploy, no config/
param change, no §4 decision. Tooling build + smoke validation only, on a bitunix branch (not polymarket).
