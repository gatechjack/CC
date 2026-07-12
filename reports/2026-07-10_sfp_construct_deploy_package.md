# Bitunix SFP construct deploy — package (ALL 4 COINS)

**2026-07-10.** Agent SSH read-only; **Board runs all prod writes.** All numbers in-sample
Binance-proxy GROSS — a forward-validation lead, not a guarantee. Scope: **full construct, all 4 coins**
(BTC/ETH/SOL/XRP), toggle-gated, inert-by-default.

---

## 1. PARITY GATE (4-coin, per-coin) — **PASS** (`_SFP_PARITY_GATE_4COIN.txt`)

The ported prod detector (`SfpModeBDetector` `swing_mode=two_candle` + `htf_ms`) reproduces the research
**per-coin** construct numbers (`SFP_P1C_HTF.txt`) **exactly** on all 4 coins at 15m AND 1h:

| coin | 15m: prod n / avgR (exp) | 1h: prod n / avgR (exp) | |
|---|---|---|---|
| BTC | 163 / −0.065 (163 / −0.065) | 149 / +0.085 (149 / +0.085) | ✅ |
| ETH | 152 / +0.342 (152 / +0.342) | 139 / +0.397 (139 / +0.397) | ✅ |
| SOL | 182 / +0.231 (182 / +0.231) | 170 / +0.073 (170 / +0.073) | ✅ |
| XRP | 165 / +0.042 (165 / +0.042) | 169 / +0.199 (169 / +0.199) | ✅ |
| POOLED | 662 / +0.137 (662 / +0.137) | 627 / +0.182 (627 / +0.182) | ✅ |

Plus: **BTC 15m signal identity 179==179 IDENTICAL**; **ATR parity** `atr(p=14)`==`_atr_series`=146.745871
(and the L3 module ports `_atr_series` verbatim → tol byte-identical). **VERDICT: PASS — SAFE TO ARM ALL 4**
(pending live forward-validation). No coin/TF mismatched — every coin arms.

---

## 2. Live-feed + deploy state (read-only checks, tonight)

- **★All 4 coins have a LIVE 1h + 1d feed** (bitunix public kline, no auth): each returns **200 bars** —
  1d spans ~200 days (2025-12-22 → 2026-07-09; ≥30 needed for D/W/M levels), 1h ~9 days. **L3 fresh-inst has a
  real 1d institutional-level source on every coin; L4 1h fire-feed has a live 1h source on every coin.** No
  `sfp_skip_no_inst_source` from a missing feed on any coin. (`_sfp_cache_livecheck.py`.)
- **main==prod byte-exact (LF)** for the 4 target files pre-deploy (`strategies.yaml 6016daea`, `main.py
  9cd99bb1`, `bitunix_sfp.py 91fd7672`, `observer 28a8a4ec` — prod == main). Deploy base clean.
- **New module `bitunix_inst_levels.py` absent on prod** (`d41d8cd9`) — clean new-file deploy.
- **PROD IS FLAT**: positions=0, open_bitunix_sfp_rows=0 (the SOL long is not open). Flat-guard re-checks at
  restart and aborts if anything is open.
- **⚠ RH pickle STALE — 57h old** (threshold ~20h) → **refresh first** (`rh_pickle_refresh.ps1`, 2FA) or the
  full restart can hard-hang the engine (~20min outage).

---

## 3. Diff A — INERT deploy (behavior-identical; all toggles default; now 4-coin caches)

Real content change = **~168 insertions / 27 deletions** across 4 files + 1 new module. (The multi-thousand
`git diff --stat` counts were CRLF artifacts; `--ignore-cr-at-eol` gives the true diff.)

**Inert proof:** `swing_mode=pivot50` → unchanged p6 `SfpDetector`; `with_trend=false` → original
`{up,range}/{down,range}` gate; `fresh_inst=false` → gate skipped; `detection_tf=15m` → `fire_on_15m=True`,
1h block + c1h refresh skipped → no extra REST calls. The new ETH/SOL/XRP 1h/1d caches are **built + polled but
unused** until `detection_tf=1h`/`fresh_inst=true` arm (they cost 6 extra light REST polls, harmless). Prod
behaves exactly as today until the armed flip.

- **`config/strategies.yaml`** (+3): `swing_mode: pivot50`, `with_trend: false`, `fresh_inst: false` (defaults;
  `detection_tf`/`tp_r` unchanged at `15m`/`2.0`).
- **`trading_corp/main.py`** (+27, 3 hunks): build `bitunix_sfp_h1_caches`/`bitunix_sfp_d1_caches` dicts for
  **all 4 coins** (BTC reuses the existing HTF caches, ETH/SOL/XRP get their own, max_bars=250); pass both
  dicts to the observer; prime+poll ETH/SOL/XRP (h1 5min, d1 30min) alongside BTC.
- **`trading_corp/agents/strategies/bitunix_sfp.py`** (+36/−9): `TwoCandleSfpDetector(SfpDetector)` (degree-2
  `_is_pivot_low` override); `SfpModeBDetector` gains `swing_mode`+`htf_ms`; engine selected by `swing_mode`;
  BOS bind `t0 = fired + self.htf_ms`.
- **`trading_corp/agents/strategies/bitunix_inst_levels.py`** (**NEW**): verbatim `InstLevels` tagger + Wilder
  `_atr_series` (period 14). **LF file** — deploy as-is (drift-check LF-normalizes).
- **`trading_corp/agents/divisions/bitunix_sfp_observer.py`** (+~90): import `InstLevels`; config gains
  `swing_mode`/`with_trend`/`fresh_inst`; ctor gains `d1_caches`/`bar_caches_1h` (per-wire `.get()` → 4-coin
  ready, no per-coin code); `_htf_ms`; detectors built with `swing_mode`+`htf_ms`; `_last_ts1h`; 1h fire-feed
  in `_warm_start_b`/`_process_symbol_b`/`process_once_master`; with-trend gate; fresh-inst gate (fail-CLOSED).

Full diffs: `git -C ~/cc diff --ignore-cr-at-eol <file>` on branch `sfp-construct-deploy-2026-07-10`.

---

## 4. Diff B — ARMED config flip (the full construct, all 4 coins)

`config/strategies.yaml` bitunix_sfp block, AFTER Diff A is inert-deployed + verified. 5 value flips + the
pre-restart halt. Because `detection_tf`/`swing_mode`/`tp_r`/`with_trend`/`fresh_inst` are **global** to the
bitunix_sfp block, this one flip arms **all 4 coins** (now that all 4 have 1h+1d caches):

```diff
@@ bitunix_sfp: @@
-  detection_tf: "15m"
+  detection_tf: "1h"                # L4: all 4 coins fire two-candle SFP on 1h (the +0.182 pooled cell)
-  swing_mode: pivot50
+  swing_mode: two_candle            # L0: degree-2 fractal (validated engine)
-  tp_r: 2.0
+  tp_r: 3.0                         # L1
-  with_trend: false
+  with_trend: true                  # L2
-  fresh_inst: false
+  fresh_inst: true                  # L3
   # and (pre-restart HALT, HOT): auto_execute: true -> false  (line 1936)
```

Rollback = each knob independently (flip + restart). `swing_mode: pivot50` returns the exact p6 engine. 15m
alternative (`detection_tf: "15m"`) fires the +0.137 pooled cell (more bars ⇒ faster forward-n); the plan
target is 1h.

---

## 5. ★ Interlock + 4-coin scope

- **~41× interlock — per-coin:** two-candle finds ~41× more swings than pivot-50. `with_trend`+`fresh_inst` are
  **global** toggles applied uniformly, so two-candle arms **only** with both gating it on **every** coin —
  never unfiltered on any coin. Satisfied by design.
- **All 4 coins now trade** (the BTC-only gap is closed): each has a live 1h fire-feed + 1d institutional
  source. Expect a fired/skip mix per coin; `sfp_skip_no_inst_source` should be **rare/zero** (only on a
  transient feed miss, not structural).
- **First-armed canary (per coin):** first 1h close → two-candle fire → 3m BOS → fresh-inst gate → per-side
  first fill (TP `/tpsl/` id, OCO clean, 3R auto-book, research-log fields). Up to ~1h to first armed watch.
- **Maiden SHORT** remains the hardest forward event (B1 slPrice sits ABOVE entry, triggers on price rising).

---

## 6. Deviations-from-backtest ledger (4-coin)

| Lever | Deviation | Status |
|---|---|---|
| L0 two-candle | none (armed = tested engine) | parity — per-coin gate (§1) |
| L1 3R | none | — |
| L2 with-trend | prod 15m **ema200** up/down vs research **macro60 daily** PS/BB | **OPEN** — trace if L2 underperforms |
| L3 fresh-inst | ATR helper parity + 1d source per coin | ATR **RESOLVED** (verbatim `_atr_series`); **1d source CONFIRMED all 4** (200 daily bars each ⇒ D/W/M resolve) |
| L4 1h | ported anchor reproduces candidate set + live 1h feed per coin | live feed **CONFIRMED all 4**; parity §1 |
| ALL | Bitunix live+fees vs Binance-perp GROSS; live n≈1 | forward-validation only |

---

## 7. Board runners — CORRECTED SEQUENCE (turnkey; helpers on Desktop)

**★Prod ~/trading_corp is NOT a git repo → deploy is scp (file-copy), not `git pull`. ★The `strategies/` dir
is phantom-UID (197609) owned → the NEW module can't be created there until a root `chown` (Azure Run Command).
Both are why the drift-check showed prod≠local — the diffs were never applied.** Corrected order:

| # | Step | Who | Runner |
|---|---|---|---|
| 0 | Refresh RH pickle (was 57h) — **DONE** | Board | `powershell -ep bypass -f "$HOME\cc\rh_pickle_refresh.ps1"` |
| 1 | **★Chown fix** (root; unblocks new module) | Board | `powershell -ep bypass -f "$HOME\Desktop\runprod.ps1" sfp_chown_fix.sh` |
| 2 | **Deploy Diff A** (5 files, inert, LF-exact) | Board | `powershell -ep bypass -f "$HOME\Desktop\sfp_deploy.ps1"` |
| 3 | Drift-check (read-only) — **all 5 rows prod==local** | any | `powershell -ep bypass -f "$HOME\Desktop\sfp_driftcheck.ps1"` |
| 4 | **Arm Diff B** (6 flips + `auto_execute:false` halt) | Board | `powershell -ep bypass -f "$HOME\Desktop\sfp_arm_config.ps1"` |
| 5 | Flat-guard + restart (aborts if not flat) | Board | `powershell -ep bypass -f "$HOME\Desktop\sfp_arm_restart.ps1"` |
| 6 | Boot smoke (read-only; two_candle/1h/3.0/true/true + 4-coin primes) | any | `powershell -ep bypass -f "$HOME\Desktop\sfp_bootsmoke.ps1"` |
| 7 | Un-halt `auto_execute:true` (HOT) | Board | `powershell -ep bypass -f "$HOME\Desktop\sfp_arm_config.ps1"` sets it false; re-enable via editprod or a one-line sed on line 1936 |

**Gate 3 is the hard stop**: if any row differs, the deploy is incomplete — do NOT arm. `sfp_deploy.ps1`
LF-normalizes each file (prod convention = LF), overwrites the 4 azureuser-owned files, and creates the new
module (needs step 1 first). `sfp_arm_config.ps1` is a block-scoped Python edit with a **hard 6-flip
assertion** (aborts + writes nothing if it can't match all 6) — dry-run confirmed exactly 6 flips on lines
1936/1941/1942/1946/1947/1948. main==prod: after step 3 passes, local main is synced to the deployed tree.
All runners parse-clean (PS5.1); chown `.sh` is ASCII/dash-safe.
