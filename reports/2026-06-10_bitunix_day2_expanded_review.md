# Bitunix Observation Window — Day-2 Expanded Review (post vol-classifier fix)

**Date:** 2026-06-10 · **Session type:** operator-supervised, read-only prod-data review
**Branch:** `bitunix-day2-expanded-review-2026-06-10` (dedicated worktree; unmerged audit trail)
**Author:** Claude (Opus) under CLAUDE.md Session discipline
**Data captured:** 2026-06-10 ~10:44 UTC (window age ≈ 31 h)

> **VERDICT (lede): window is CLEAN. No hard-stop tripped.** The newly-unlocked 3–5%
> ATR band is the *only* band trading this window and it is performing well (81% win,
> +0.18R/trade, +$0.94). F-5 classifier fix is **confirmed active and correct**. Two
> non-blocking watch-items (one bitunix-adjacent rate-limit, one out-of-scope logging
> bug) and one standing strategy-design observation (sub-1R wins / TP3 never reached).
> **Window continues to Day-5; no operator action required to keep it running.**

---

## 0. Scope & constraints

- **Read-only against prod throughout.** No code changes, no prod writes, no config
  changes. Local artifacts only: this report + a deploy_log F-5 watch-item confirmation
  (Q5 met the condition).
- **SSH execution note:** the operator was away from PowerShell, so this agent executed
  the read-only streamers directly (operator-authorized in-session). All queries are
  `SELECT`/`grep`/`journalctl` reads. Scripts: `s3.sh`,`s4.sh`,`s5.sh`,`s6.sh`,`j1.sh`
  (repo root, untracked).
- **Out of scope:** any fix or tuning (findings inform operator decisions only);
  Polymarket; Day-5 close-out.

### Hard stops (abort + surface if any trip) — **ALL CLEAR**
| # | Trigger | Result |
|---|---|---|
| 1 | Any live-mode primitive firing in paper | **0 rows** (s3 HS1) ✅ |
| 2 | `execution_mode` ≠ paper | **paper** — config L1022 + boot-log ×2 (j1 JC0/JC4) ✅ |
| 3 | Runaway fire rate (>3× ≈ >30/day) | **~12/day** (~1.2× anchor) — elevated, not runaway ✅ |

---

## 1. State verification (complete)

| Item | Value | Source |
|---|---|---|
| Local `origin/main` HEAD | `32aa884dcdbb5b7801a43bb7758a6672449ef490` | `git rev-parse origin/main` |
| Deployed F-5 fix merge | `7834375` ("…P1 vol-classifier wiring fix") | deploy_log 2026-06-09 |
| Fix ∈ origin/main? | **Yes** — `merge-base --is-ancestor 7834375 origin/main` → exit 0 | git |
| Working isolation | dedicated worktree `bitunix-day2-expanded-review-2026-06-10` off origin/main | `git worktree add` |
| Window start | **2026-06-09 03:49:41 UTC** (MainPID 2397472) | deploy_log / boot log |
| Window close (Day-5) | 2026-06-14 03:49 UTC | 5-day window |
| Restart interruption | ~14:49 UTC 06-09 (RH pickle regen, MainPID 2427161/2427175) | deploy_log / boot log |
| Pre-bug fire-rate anchor | ~9–10/day (06-02 traded 9×, ATR 2.92%) | BACKLOG P1 |
| Day-1 check-in (given) | 3 fires, 3/3 wins, avg R 0.644 | operator (in-prompt) |
| `_atr_pct_to_tier` (fix) | final boundary reads `extreme` (5.0%); verified `bitunix_htf_regime.py:743-751` | source this session |

> **Anomaly (low-severity, handled):** the `--worktree` launch resolved into the **main
> checkout on `main`**, not a per-session worktree. A dedicated worktree was created
> explicitly before any commit (no branch-hijack; main HEAD untouched).

---

## 2. Findings by question

### Q1 — Trade inventory & fire rate

| Metric | Value |
|---|---|
| Total fires (paper_trade_record) | **16** (all resolved; 0 open) |
| Per-day | 06-09: **11** · 06-10: **5** (partial, through 09:12Z) |
| Span | 2026-06-09 04:57:02 → 2026-06-10 09:12:01 (1.177 d) |
| W / L | **13 W / 3 L** (81.3% win) |
| Expectancy (all-in) | **+0.176 R/trade** |
| Cumulative P&L | **+$0.94** |
| Avg win R | 0.448 · Max R 0.902 |
| Audit cross-check | `would_have_placed`=16, `placed`=16, `trade_plan_decision`=19 (3 plans rejected = `skipped_trade_plan`=3). Ledger consistent. |

**Fire-rate read:** ~12/day vs the 9–10/day pre-bug anchor → **elevated ~1.2×, NOT
runaway** (runaway line ≈ 30/day). The modest lift is the expected consequence of the
3–5% band being live (it was dead for the strategy's entire prior life; BTC ATR has sat
~4% all window). **Normal-to-slightly-elevated. No concern.**

### Q2 — ATR-band performance split (<3% vs 3–5%) — *load-bearing*

Per-trade ATR recovered by joining each trade's `source_signal` → `htf_gate_decision.atr_pct_d1`
(nearest ts ≤180 s; join verified 1:1, n=16, no fan-out).

| Band | n | W | L | Win% | Expectancy R | Σ PnL | ATR range |
|---|---|---|---|---|---|---|---|
| **3–5%** (newly unlocked) | **16** | 13 | 3 | 81.3% | **+0.176** | +$0.94 | 3.977–4.152% |
| <3% (always-tradeable) | **0** | — | — | — | — | — | — |
| ≥5% (still hard-zeroed) | 0 | — | — | — | — | — | — |

**The load-bearing read:** the entire fresh window traded **exclusively in the 3–5%
band** — BTC 1D ATR has been pinned ~4% throughout, so **there is no <3% cohort this
window to compare against.** The intra-window "3–5% vs <3%" comparison the question
anticipated cannot be computed (no <3% samples).

What we *can* say: the 3–5% band on its own is **performing well** — 81% win rate,
positive +0.18R expectancy, positive PnL. Against the only <3% reference we have (the
pre-bug 06-02 day: 9 fires, ~5W/1L at ATR 2.92% per the old Day-2 audit), the 3–5% band's
win-rate is comparable-or-better. **There is no evidence the 3–5% band performs
materially worse, and therefore no data-driven case to tune the 5.0 `extreme` knob
down.** The sub-1R expectancy is a TP-structure issue (Q3), not a band-quality issue, and
would affect <3% trades identically.

Per-trade detail (all `sell`, all tier `high`, all ATR ~4%):

| oid | ts | signal | tier | result | R | ATR% | legs |
|---|---|---|---|---|---|---|---|
| cf40deeb | 06-09 04:57 | cvd_bear_flip | STANDARD | win | 0.902 | 3.985 | tp1,tp2 |
| 171d7a46 | 06-09 05:58 | mc_b_sell_circle_div | STANDARD | win | 0.201 | 3.977 | tp1 |
| e281afd8 | 06-09 09:37 | mc_a_blood_diamond | STANDARD | win | 0.828 | 4.016 | tp1,tp2 |
| a0655243 | 06-09 13:39 | mc_a_red_diamond | STANDARD | **loss** | -1.0 | 4.052 | [] |
| a7a84015 | 06-09 18:33 | mc_b_sell_circle_div | STANDARD | **loss** | -1.0 | 4.089 | [] |
| f7ed2249 | 06-09 19:45 | mc_b_sell_circle_div | STANDARD | win | 0.142 | 4.076 | tp1 |
| c51a18c5 | 06-09 20:21 | mc_b_sell_circle | STANDARD | **loss** | -1.0 | 4.065 | [] |
| 077b0c8b | 06-09 21:18 | mc_a_redx | STANDARD | win | 0.820 | 4.066 | tp1,tp2 |
| 3e18fec7 | 06-09 22:01 | mc_a_red_diamond | STANDARD | win | 0.164 | 4.083 | tp1 |
| 63c2ed27 | 06-09 22:55 | mc_a_redx | STANDARD | win | 0.207 | 4.084 | tp1 |
| 6c68af8c | 06-09 23:34 | mc_a_blood_diamond | PREMIUM | win | 0.125 | 4.089 | tp1 |
| c6adb85c | 06-10 00:37 | mc_a_red_diamond | PREMIUM | win | 0.541 | 4.114 | tp1,tp2 |
| 2c81914d | 06-10 02:12 | mc_a_red_diamond | PREMIUM | win | 0.794 | 4.115 | tp1,tp2 |
| 4f1bfab9 | 06-10 02:45 | cvd_bull_flip | STANDARD | win | 0.133 | 4.136 | tp1 |
| a1d131b2 | 06-10 03:30 | mc_b_buy_circle | STANDARD | win | 0.201 | 4.123 | tp1 |
| 2691a747 | 06-10 09:12 | mc_a_red_diamond | PREMIUM | win | 0.764 | 4.152 | tp1,tp2 |

### Q3 — R distribution / TP-leg fill

| Deepest leg reached | n | W | L | Avg R | Σ PnL |
|---|---|---|---|---|---|
| TP3 (full runner) | **0** | 0 | 0 | — | — |
| TP2 (not TP3) | 6 | 6 | 0 | **0.775** | +$1.32 |
| TP1 only | 7 | 7 | 0 | **0.167** | +$0.23 |
| None — stopped out | 3 | 0 | 3 | **-1.0** | -$0.61 |

- **avg win R = 0.448, max single R = 0.902 — every win is still sub-1R** (Day-1's
  observation holds at N=16). **TP3 was never reached.**
- The win structure is "many small partials": 7 of 13 wins are TP1-only at ~0.17R (TP1
  fills a fraction, SL ratchets to BE, stops at BE). The 6 TP2 wins (~0.78R) carry the
  P&L. Net expectancy is positive but thin precisely because the big TP3 leg never runs in
  this ~4% ATR regime.
- **Strategy-design observation (for operator/strategy, not this session):** realized R is
  structurally capped well under 1.0 — candidate causes are TP1 too tight, TP3 too far for
  the current vol regime, or `max_hold` expiring before TP3. This is the single most
  material *forward* question for any eventual flip-readiness call, and it is band-agnostic.

### Q4 — Hard-stop checks

| Check | Result |
|---|---|
| Live-mode primitives in paper | **0** (s3 HS1) ✅ |
| `agent_error` rows since window | **0** (s3 Q4) ✅ |
| `execution_mode` | **paper** — `strategies.yaml:1022` + boot logs 03:49:45 & 14:49:01 ✅ |
| DB-lock retries (journal) | **0** across both paths (logger + `insert_paper_trade_record`); 0 fallback writes — vs the 8/day Day-2 baseline. Cleaner than baseline. ✅ |

> In-scope error note: `paper_trade_replay` logged **1** ERROR in-window — `replay failed
> for order_id=171d7a46 … bitunix kline err: code=10006 'request too frequently'`
> (06:03:41). The replay retried and that trade resolved (win, +0.201R). Transient
> rate-limit, self-recovered — see Q6 watch-item.

### Q5 — Classifier sanity — **F-5 CONFIRMED**

| Check | Result |
|---|---|
| `htf_gate_decision` rows in window | **41** — **41/41 `high`**, ATR 3.977–4.152% |
| Band violations (extreme<5 / high≥5) | **0** (s3 Q5b) ✅ |
| Unknown tiers (atr=None / SAFE_MODE) | **0** — incl. across the 14:49Z restart (d1-cache reprimed by 14:59Z, ATR 4.0%) ✅ |
| Near-5% boundary rows (ATR 4.5–5.5) | **0** — no flap risk; ATR nowhere near the cutoff ✅ |
| Vol-tier hard-zeros (`vol_tier_extreme`) | **0** |

The 22 size-0 `htf_gate_decision` rows are zeroed by **`proximity_to_support`** or
**`regime_forbids_side`** — *separate, correct* directional/structure gates — **never by
the vol-tier**. This is exactly the F-5 watch-item criterion met: ATR in [3.0, 5.0) →
`volatility_tier="high"`, `size_multiplier=1.0`, no `hard_zero_reason="vol_tier_extreme"`,
and firing resumed (first since 06-02 22:15Z). **The classifier output is correct in the
3–5% band.**

### Q6 — Anomaly sweep

`bitunix_score_decided` outcome distribution (497 decisions):

| Outcome | n | Note |
|---|---|---|
| skipped_pa_validation | 273 | PA gate standing aside (well-calibrated validator) — normal |
| skipped_score | 145 | below score threshold — normal |
| skipped_cooldown | 38 | post-fire cooldown — normal |
| skipped_htf_gate | 22 | = the 22 proximity/regime size-0 rows (Q5) — normal |
| **placed** | **16** | = the 16 fires — consistent |
| skipped_trade_plan | 3 | v2 plan rejected — minor |

- **Reconciler mismatch / divergence kinds: 0.** ✅
- **No `rejected_risk`, no `error_*` outcomes.** ✅
- **journalctl (bitunix observer):** clean except a recurring BitUnix **`code=10006
  'request too frequently'`** rate-limit WARNING on account/balance polls (USDT/USDC),
  clustered 16:09–17:45 and 20:00–22:01 on 06-09. Non-fatal (retried); same theme as the
  one replay ERROR in Q4.

**Peripheral / out-of-scope findings (NOT bitunix; recorded for operator awareness):**

1. **123× `TypeError: not all arguments converted during string formatting`** in-window
   (≈ the 121 service-wide tracebacks). This is a **logging `%`-format bug**, not a
   trading failure — frames are `logging/__init__.py format→getMessage→emit`, originating
   in the **tastytrade streamer** (`tastytrade/streamer.py:434 _reader`, ~74×) and
   **starlette** exception handler (~42×). Python logging **emits the record anyway** after
   printing the traceback, so it is cosmetic stderr noise. **It does not touch the bitunix
   path and does not corrupt window data** (audit/DB writes are independent of stderr). The
   earlier "3 bitunix-adjacent tracebacks" were false-positive proximity matches. → candidate
   P3 cleanup (find the bad `%`-format log call in the tastytrade/starlette path).
2. **Fidelity broker-connect failure at startup** (`BrowserType.launch: ENOENT …
   ms-playwright/firefox … lock`) — known Playwright/Firefox issue, separate division.
3. **Robinhood `'NoneType'…get` connect errors at 03:53** — from the first (stale-pickle)
   boot; resolved by the 14:49Z pickle regen.

---

## 3. Verdict

**(a) Window health: CLEAN.** All three hard stops clear; zero live primitives, zero
agent_errors, zero reconciler mismatches, zero db-lock retries, `execution_mode=paper`
triple-confirmed. Two non-blocking watch-items: a bitunix-adjacent BitUnix API
rate-limit (`code=10006`, self-recovering) and an out-of-scope cosmetic logging TypeError
(123×, tastytrade/starlette, no data impact). Neither corrupts the observation window.

**(b) 3–5% band read: performing well; no knob change indicated.** The newly-unlocked
band is the *only* band that traded (ATR ~4% all window) — 16 trades, 81.3% win,
**+0.176R** expectancy, **+$0.94**, ATR 3.977–4.152%. No <3% cohort exists this window to
compare against, so the anticipated "materially worse?" test is inconclusive *by absence
of <3% data*, but the band's standalone performance is healthy. **No data-driven case to
lower the 5.0 `extreme` cutoff.** The thin expectancy is a TP-structure matter (Q3), not a
band matter.

**(c) Pause/operator action before Day-5: none required to keep the window running.**
Recommended (non-urgent) operator awareness items:
1. **BitUnix `code=10006` rate-limit pressure** (in-scope watch-item) — currently
   self-recovering (1 replay ERROR retried fine; account-poll WARNINGs). Monitor; if it
   grows, consider poll-cadence backoff. Relevant because snapshot-failure falls back to
   the placeholder-equity sharp edge.
2. **Logging TypeError (123×/window)** — file as a P3 cleanup (tastytrade/starlette
   `%`-format). Out of scope to fix here.
3. **Strategy-design: sub-1R wins / TP3 never reached** — the key forward question for the
   eventual flip-readiness decision; band-agnostic. Belongs to a strategy/backtest review,
   not the window-health track.

**F-5 watch-item:** Q5 confirms classifier output correct in the 3–5% band → the
deploy_log 2026-06-09 F-5 watch-item is updated to CONFIRMED on this branch.

---

## 4. Appendix — key raw outputs (verbatim)

```
# s3 HARD-STOP BLOCK
HS1 live-mode primitives: (0 rows)
HS3: total_fires=16  first=2026-06-09T04:57:02Z  last=2026-06-10T09:12:01Z  span_days=1.177

# Q1b outcomes
result  n   avg_r  sum_pnl
win     13  0.448  1.55
loss     3  -1.0   -0.61

# s6 Q2-BUCKET
band  n   wins losses win%  expectancy_r sum_pnl atr_min atr_max
3-5%  16  13   3      81.3  0.176        0.94    3.977   4.152

# s4 Q3-TPLEG
2_TP2_not_TP3   6  6 0   0.775  1.32
3_TP1_only      7  7 0   0.167  0.23
4_none_stopped  3  0 3  -1.0   -0.61

# s4 Q1-EXPECTANCY
n_resolved=16 wins=13 losses=3 win_pct=81.3 expectancy_r_allin=0.176 cum_pnl=0.94 avg_win_r=0.448 max_r=0.9024

# s3 Q5a vol_tier by day            # s3 Q5b band_violations=0 ; Q5c unknown=(none) ; Q5d near-5%=(none)
2026-06-09  high  23  3.977 4.089
2026-06-10  high  18  4.114 4.152

# s3 Q6a bitunix_score_decided outcomes
skipped_pa_validation 273 | skipped_score 145 | skipped_cooldown 38 | skipped_htf_gate 22 | placed 16 | skipped_trade_plan 3
# Q6b reconciler/mismatch/divergence: (none)

# j1 JC0/JC1   execution_mode: paper (strategies.yaml:1022) ; db-lock retries: 0/0/0 ; fallback writes: 0
# s6 TBX-msg   123  TypeError: not all arguments converted during string formatting
#              top non-stdlib frames: main.py:3535 (122), tastytrade/streamer.py:434 _reader (74), starlette/_exception_handler.py:42 (42)
# s6 TBY in-scope ERROR: paper_trade_replay replay failed order 171d7a46 — bitunix kline 10006 'request too frequently' (retried OK)
```

*Scripts (read-only) retained in repo root: `s3.sh` (probe+hard-stops), `s4.sh` (Q1/Q3
aggregates), `s5.sh`/`s6.sh` (Q2 join + traceback forensics), `j1.sh` (execution_mode +
db-lock + journal scan).*
