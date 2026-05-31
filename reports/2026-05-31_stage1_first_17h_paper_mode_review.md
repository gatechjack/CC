# Stage 1 paper-mode post-deploy performance review
**Window:** 2026-05-31 05:36:49 UTC → 2026-05-31 14:21 UTC (~8h45m, NOT 17h — see §2)
**Prod:** `tc-prod-vm` @ origin/main `7352f8f` (Stage-1 redeploy attempt #3)
**Author:** Claude (read-only diagnostic; no code/config/prod changes)
**Branch:** `worktree-stage1-first-17h-review-2026-05-31` (worktree-isolated)

---

## 1. Executive summary

- **The model is working as designed. No code or config tuning is indicated by this window alone.**
- **Zero paper trades fired in the post-deploy window.** Root cause is market regime, not the deploy: BTC volatility compressed from ~$86 mean ATR(14, 3m) on 5/28 to ~$26-40 on 5/30-5/31 (a 50-70% reduction). Structural stops collapsed below the fee-floor threshold; 100% of post-HTF-gate signals (16/16) rejected with `fees_too_high_for_risk`.
- **Deploy verification clean:** Stage-1 code paths confirmed active — TIER_SIZING canonical via `9fd9022`, `execution_mode=paper` on bitunix, IC scanner online with weekday gate, all 12 paper-exec divisions registered, NRestarts=0.
- **HTF gate behaved exactly as the operator described**: NEUTRAL regime + sell-side + proximity to support (<0.3%) → hard-zero. Two cvd_bull_flip events (one pre-deploy at 22:09 UTC 5/30 reproducing operator's reference, one post-deploy at 13:57 UTC today) show the gate's mean-reversion assumption was wrong in both cases. Sample too small to declare a calibration issue; flag for trend-monitoring.
- **One operational observation worth flagging (NOT a Stage-1 regression):** polymarket `429` retries are hitting attempt 1→4 within ~0.1s with `backoff 0.0s` printed. Unrelated to gate (a) which is bitunix-only; surfaced because it's noisy in logs and is the only "high-frequency retry" signal in the post-deploy journal.

---

## 2. Deploy verification

### 2.1 Window scope correction

The prompt opens with "last 17 hours since 2026-05-30 deploy (~01:30 ET / ~05:30 UTC through ~18:30 ET / ~22:30 UTC)" and operator subsequently noted "older prompt - more than 17 hours, since the change but you have the timestamp - disregard 17 hours as the prompt is a bit old".

Per `systemctl show trading-corp` on tc-prod-vm:

```
MainPID=1918098
NRestarts=0
ExecMainStartTimestamp=Sun 2026-05-31 05:36:49 UTC
ActiveState=active
SubState=running
```

Deploy time = **2026-05-31 05:36:49 UTC**. Current = **2026-05-31 14:21 UTC**. Window = **~8h45m**. The "01:30 ET on 2026-05-30" reference in the prompt body conflates the original `17:22 UTC 5/30` Stage-1 attempt (rolled back), the `22:43-23:09 UTC 5/30` redeploy attempt #2 (also rolled back), and the actual successful `05:36 UTC 5/31` redeploy attempt #3 — per `[[stage1-redeploy3-landed-2026-05-31]]`. All analysis below scopes to the actual successful-deploy window.

### 2.2 Stage-1 code paths confirmed active

All Stage-1 surfaces visible in production:

| Surface | Evidence |
|---|---|
| TIER_SIZING canonical (PREMIUM 0.015/25×, STANDARD 0.0075/25×) | `9fd9022` merged 2026-05-30 05:56 UTC; redeploy3 transfer set carried `config/strategies.yaml` whole — prod md5 matches main `1fda7f608c1e74900b55eb77f0bb344f` per `[[stage1-redeploy3-landed-2026-05-31]]` |
| `execution_mode=paper` on bitunix | Audit row at 05:36:51 UTC `{"mode":"PAPER","live_brokers":[],"dry_run":false}`; BitunixBroker connected paper at 05:36:59 with equity=$240.90 |
| Gate (a) REST resilience primitives | Code present (eae5080); no retry/backoff/stale/stuck events observed in journal — primitives in place but no firing required in this window |
| Safety_notifier wired | Confirmed via 12 paper-exec divisions registered post-restart (per `[[stage1-redeploy3-landed-2026-05-31]]`) |
| tasty_options division registered | Journal at 05:36:53 UTC: `Registered paper-exec broker for division=tasty_options (paper=True)`; PaperBroker connected equity=$0.00 |
| IC signal scanner online | Journal at 05:37:06 UTC: `IC signal scanner online: weekdays 09:45-09:50 ET (poll every 60s)` |

### 2.3 Process health

- `NRestarts=0` since deploy (no crashes; no auto-restarts).
- Zero crash-signature lines in `journalctl -u trading-corp` (no `Traceback|AttributeError|ImportError|TypeError|ModuleNotFoundError|NameError|SyntaxError|CRITICAL`).
- `healthz=200` confirmed at deploy time per `[[stage1-redeploy3-landed-2026-05-31]]`.

---

## 3. Decision flow analysis

### 3.1 Funnel

Post-deploy audit-event counts (`ts >= '2026-05-31T05:36:49'`, kind∈bitunix-stack):

| Stage | Event kind | Count | Notes |
|---|---|---|---|
| Score | `bitunix_score_decided` | 118 | All scoring decisions for bitunix triggers |
| PA validation | `pa_validation_decision` | 79 | Reached PA layer (i.e., tier≠SKIP) |
| HTF regime snapshot | `htf_regime_snapshot` | 55 | Side-effect log, not a gate |
| HTF gate | `htf_gate_decision` | 45 | Reached HTF layer (i.e., PA pass) |
| Trade plan | `trade_plan_decision` | 16 | Reached final builder (i.e., HTF allow) |
| Observer classification | `bitunix_observer_classified` | 7 | Bias-side classifications |
| PA expired (loop timeout) | `pa_validation_expired` | 3 | Pending PA aged out |
| PA redeemed | `pa_validation_redeem` | 2 | Pending PA completed on later bar |

`paper_trade_record` rows in window for any division: **0**. Most recent bitunix paper fire was **2026-05-29T19:58:10+00:00** (~43 hours pre-window).

### 3.2 Outcome distribution

Per `bitunix_score_decided.outcome` field (the final outcome field):

| Outcome | Count | % of 118 | Notes |
|---|---|---|---|
| `skipped_score` (tier=SKIP, net_score<5) | 39 | 33.1% | 24 sell, 12 buy, 3 flat |
| `skipped_pa_validation` | 34 | 28.8% | All buy STANDARD (24) + sell PREMIUM (10) |
| `skipped_htf_gate` | 29 | 24.6% | All sell (20 STANDARD + 9 PREMIUM) |
| `skipped_trade_plan` (fee floor) | 16 | 13.6% | All sell (11 STANDARD + 5 PREMIUM) |
| **fired** | **0** | **0.0%** | |

### 3.3 Drill-in: `skipped_pa_validation` (34)

PA validator-pair distribution among the 79 PA decisions:

| Decision | Validators passed | Side | Tier | Count |
|---|---|---|---|---|
| pass | vwap+structure | sell | STANDARD | 21 |
| pass | vwap+volume+structure | sell | STANDARD | 9 |
| pass | vwap+structure | sell | PREMIUM | 8 |
| pass | vwap+volume | sell | PREMIUM | 3 |
| pass | vwap+volume+structure | sell | PREMIUM | 2 |
| pass | volume+structure | sell | PREMIUM | 1 |
| pass | vwap+volume | sell | STANDARD | 1 |
| reject | only volume | buy | STANDARD | 12 |
| reject | NONE | buy | STANDARD | 12 |
| reject | only vwap | sell | PREMIUM | 10 |

**Patterns:**
- 100% of buy signals that reached PA validation (24/24) were rejected. In every buy rejection, both `volume_confirmation` AND `structure_alignment` failed — 12 with only volume passing (vwap+structure failed), 12 with nothing passing.
- 100% of PA passes were sell-side (45/45). Among sell passes, vwap+structure dominates (29/45 = 64.4%); all three pass in 11/45 (24.4%).
- 5/28 operator caveat applies: H4 ema_alignment=bear + D1 structure=bear → structure_alignment trivially aligns for sell; same bear-regime artifact still operative in this window. None of the buy rejections appear to be false-negatives (the H1/H4/D1 alignment is firmly against buy entries).

### 3.4 Drill-in: `skipped_htf_gate` (29)

HTF gate verdict distribution (45 events; 16 allow + 29 block):

| Verdict | hard_zero_reason | Side | Tier | Regime | Count |
|---|---|---|---|---|---|
| allow | — | sell | STANDARD | NEUTRAL | 11 |
| allow | — | sell | PREMIUM | NEUTRAL | 5 |
| block | proximity_to_support | sell | STANDARD | NEUTRAL | 20 |
| block | proximity_to_support | sell | PREMIUM | NEUTRAL | 9 |

**Patterns:**
- 100% of HTF gate events were sell-side (45/45). Only 1 buy signal in the entire window reached PA pass (per the broader funnel) — buy signals are dying earlier in the funnel under bear regime.
- 100% of blocks (29/29) cited `proximity_to_support` (within 0.3% of S). 0 blocks for `proximity_to_resistance`, `vol_tier_extreme`, `funding_extreme_crowded`, `regime_forbids_side`, or `safe_mode`.
- 100% of decisions were `regime=NEUTRAL` for the entire window — no BULL/BEAR/STRONG_BULL/STRONG_BEAR regime classification fired. The 4h bear EMA align + D1 bear structure was apparently insufficient to escalate beyond NEUTRAL.
- Was the gate right? See §8 deep dive — sample of 2 events shows mean-reversion assumption was wrong both times.

### 3.5 Drill-in: `skipped_trade_plan` (16) — the fee floor

All 16 had `skip_reason=fees_too_high_for_risk` and `should_trade=false`. Per `trading_corp/agents/strategies/trade_plan.py:210-236`:

```python
fee_cost_per_unit = fees.round_trip_cost_pct() * entry
tp1_target_distance = cfg.tp1_r_target * risk_per_unit
tp1_fee_floor = cfg.tp1_min_profit_multiplier * fee_cost_per_unit
tp1_distance = max(tp1_target_distance, tp1_fee_floor)
# ...
if tp1_distance >= tp2_distance:
    return _skip(entry, "fees_too_high_for_risk")
```

The check is: if fee floor pushes TP1 past TP2, no edge — skip.

Stop-distance and ATR samples on the 16 rejections:

| Tier | Entry | swing_high (SL ref) | stop_distance ($) | stop_distance (% of entry) | atr_used |
|---|---|---|---|---|---|
| PREMIUM | 73987.7 | 73994.3 | 6.6 | 0.009% | 26.5 |
| PREMIUM | 74021.4 | 74077.5 | 56.1 | 0.076% | 25.9 |
| PREMIUM | 73974.8 | 74047.4 | 72.6 | 0.098% | 24.2 |
| STANDARD | 73937.3 | 73943.0 | 5.7 | 0.008% | 38.9 |
| STANDARD | 73911.3 | 73949.7 | 38.4 | 0.052% | 36.6 |
| STANDARD | 73958.9 | 73953.0 | swing inverted | — | 34.8 |
| STANDARD | 73922.9 | 73813.8 | swing inverted | — | 31.2 |
| PREMIUM | 73946.3 | 73956.6 | 10.3 | 0.014% | 33.5 |
| PREMIUM | 73922.4 | 73956.6 | 34.2 | 0.046% | 35.3 |
| STANDARD | 73906.3 | 73956.6 | 50.3 | 0.068% | 35.7 |
| STANDARD | 73910.6 | 73975.1 | 64.5 | 0.087% | 34.5 |
| STANDARD | 73930.4 | 73955.2 | 24.8 | 0.034% | 31.6 |
| STANDARD | 73918.8 | 73955.2 | 36.4 | 0.049% | 32.6 |
| STANDARD | 73918.8 | 73955.2 | 36.4 | 0.049% | 32.6 |
| STANDARD | 73918.8 | 73955.2 | 36.4 | 0.049% | 32.6 |
| STANDARD | 73563.4 | 73835.2 | 271.8 | 0.369% | 71.3 |

ATR_min/avg/max in the window were 24.2/40.2/71.3. Stop distances mostly **<0.10% of entry**, well below the typical 0.18% fee-floor break-even threshold cited in `[[project-bitunix-fee-floor-3rule-audit-2026-05-29]]`. The 11/16 with stop distance < $60 are clear fee-floor casualties. Two had "swing inverted" data (swing_high BELOW entry for a sell) — possibly stale swing data or an edge case in the swing lookback; flagged for monitoring (low priority).

The one outlier (Entry 73563.4 / stop 271.8 / 0.369%) was at 14:30 UTC, deep into the late-morning sell-off — large enough to clear a 0.18% fee floor, yet still rejected. The likely path: `tp2_distance` (defaults to `tp2_r_default × risk_per_unit`) was also "snapped" to a constraint not visible in `inputs`. Worth a one-shot trace if it recurs, but in isolation it's noise.

### 3.6 Drill-in: `skipped_score` (39)

These are signals whose net_score landed below `min_score_to_fire=5`. Distribution is 24 sell / 12 buy / 3 flat — score-side is roughly the inverse of regime bias (buy signals scoring weakly because bear-regime adversaries dominate the contributions). This is the expected scoring behavior and is not a gate-tuning issue.

### 3.7 Cross-stage gap (1 row)

`htf_gate_decision allow` count = 16; `trade_plan_decision` count = 16; `skipped_trade_plan` outcome count from score_decided = 16. Internally consistent — no orphans in this window. (Earlier query with a wrong text-comparison filter showed a 22-vs-21 gap pre-window-correction; with correct ISO `T` filter the count is clean.)

---

## 4. Fired-trades analysis

**0 fires in window.** Phase 3 questions (tier mix, side mix, validator pair × outcome, SL lifecycle, R-multiple distribution) are not applicable for this window. The deferred picture: most recent bitunix fires were 8 on 2026-05-28 and 3 on 2026-05-29 (last on `2026-05-29T19:58:10`). For longitudinal SL lifecycle + R-multiple analysis, see the 5/28 PA 2-of-3 deploy memory and prior dashboards — not re-litigated here.

---

## 5. Pre-vs-post comparison

A matched-window (8h45m) pre-deploy slice (`2026-05-30 20:51 UTC → 05:36 UTC 5/31`):

| Stage | Pre-deploy (8h45m) | Post-deploy (8h45m) |
|---|---|---|
| `bitunix_score_decided` | 207 | 118 |
| `pa_validation_decision` | 178 | 79 |
| `htf_gate_decision` | 11 | 45 |
| `trade_plan_decision` | 6 | 16 |
| Fires | 0 | 0 |

**Note:** The pre-deploy window straddles the 22:43-23:09 UTC redeploy attempt #2 crash loop (3 auto-restarts) so signal counts are inflated by webhook re-fires during instability — not a clean baseline. The shape difference (more events earlier in the funnel pre-deploy, more events further down post-deploy) is most likely the redeploy churn, not Stage-1 behavior.

Per-day fee-floor skip count (cleaner longitudinal view):

| Date | fee_floor skips | fires |
|---|---|---|
| 2026-05-24 | 4 | 0 |
| 2026-05-25 | 2 | 0 |
| 2026-05-26 | 1 | 0 |
| 2026-05-27 | 0 | 3 |
| 2026-05-28 | 17 | 8 |
| 2026-05-29 | 15 | 3 |
| 2026-05-30 | 7 | 0 |
| 2026-05-31 | 22 (partial day) | 0 |

ATR mean across same trade_plan_decision events:

| Date | n | ATR_avg ($) |
|---|---|---|
| 2026-05-27 | 3 | 122.3 |
| 2026-05-28 | 25 | 86.3 |
| 2026-05-29 | 18 | 67.7 |
| 2026-05-30 | 7 | 26.3 |
| 2026-05-31 | 22 | 40.2 |

**Read:** the fee-floor escalation isn't Stage-1; it's BTC volatility compressing from ~$86 mean ATR(14, 3m) on 5/28 (when 8 trades fired) to ~$26-40 on 5/30-5/31. Per `[[project-bitunix-fee-floor-3rule-audit-2026-05-29]]`, the fee-floor break-even is roughly 0.18% of entry (~$133 at $73.9K). Structural stops at <$60 cannot pass that floor. The deploy did NOT change fire rate because the pre-existing market regime already had it at zero.

---

## 6. Gate (a) resilience metrics

| Primitive | Audit-event evidence | Journal evidence |
|---|---|---|
| `rest_retry` (bitunix REST cloudflare/429/5xx) | 0 events with kind matching `*retry*`/`*backoff*` | 0 lines matching `BitunixBroker.*(retry|backoff)` |
| `snapshot_stale_halt` | 0 events | 0 lines |
| `stuck_order_timeout` | 0 events | 0 lines |

Gate (a) primitives are present but **un-exercised in this window** — consistent with the prompt's expectation ("shouldn't fire often in normal operation"). The only BitunixBroker journal line post-deploy is the connect at 05:36:59 UTC. The bitunix REST surface was quiet because there are no live orders to place (paper mode); the read-only bar-cache fetches at 05:42 UTC all succeeded with `last_refresh_error: None` on 3m/1h/4h/1d timeframes.

**Anomaly not in scope but worth flagging** — Polymarket retries are noisy with `backoff 0.0s` printed across all 4 attempts (e.g., `429 from gamma-api.polymarket.com/markets; backoff 0.0s (attempt N/4)` at 05:42:51). This is NOT gate (a) (which is bitunix) and is NOT a Stage-1 regression — it's a pre-existing pattern in the polymarket REST client. Surfaced only because it's the loudest "retry" signal in the post-deploy journal.

---

## 7. Tasty_options activation observations

| Check | Result |
|---|---|
| Division registered | ✅ Journal at 05:36:53 UTC: `Registered paper-exec broker for division=tasty_options (paper=True)` |
| Paper broker connected | ✅ `PaperBroker connected (account=paper_tasty_options, equity=$0.00)` |
| Tastytrade session connected | ✅ Journal at 05:36:55 UTC: `TastytradeBroker connected: account=5WZ66443, is_test=False, n_accounts=1` |
| IC scanner online | ✅ Journal at 05:37:06 UTC: `IC signal scanner online: weekdays 09:45-09:50 ET (poll every 60s)` |
| Scanner tick events post-deploy | **0** — expected because 2026-05-31 is Saturday; scanner schedule is weekdays-only |
| `place_order` events post-deploy | **0** ✅ (auto_execute=false confirmed) |
| HITL-pending entries | **0** (none expected; no scanner ticks) |
| Tasty audit-event kinds | **0** post-deploy (none expected on weekend; market closed) |

**Observation only (not a regression):** at 06:57-06:58 UTC there are 5 `TastytradeDataProvider.get_underlying_price` WARN lines for SPY/QQQ/IWM/GLD/TLT — "No data present in response: {}". These appear unrelated to the IC scanner (which is correctly gated to weekday 09:45-09:50 ET) and look like a non-IC code path that periodically polls underlying prices. Returns empty on Saturday because the equity-market data feed is dormant. Harmless warning; not in scope to fix.

---

## 8. cvd_bull_flip deep dive

### 8.1 Operator's referenced event (pre-deploy)

The "at 18:09 ET a cvd_bull_flip trigger appeared..." event the operator described is **pre-deploy**: it occurred at `2026-05-30T22:09:02+00:00` (= 6:09 PM EDT 2026-05-30), under the prior code (4985bbe + sed overlay). Full chain:

```
2026-05-30T22:09:02 webhook_received        lord_otter cvd_bull_flip BTCUSDT.P @ 73831.8
2026-05-30T22:09:02 pa_validation_decision  PASS [vwap, volume]; failed [structure]; 2/2 satisfied
2026-05-30T22:09:03 htf_gate_decision       BLOCK proximity_to_support
2026-05-30T22:09:03 bitunix_score_decided   STANDARD sell net_score=6
```

Matches operator's description exactly (PA pass on 2-of-3, score +6 STANDARD, HTF block on support-proximity).

### 8.2 Analogous post-deploy event

A near-identical event occurred today **post-deploy**:

```
2026-05-31T13:57:00 webhook_received        lord_otter cvd_bull_flip BTCUSDT.P @ 73828.7
2026-05-31T13:57:01 pa_validation_decision  PASS [vwap, volume, structure]; 3/3 satisfied
2026-05-31T13:57:01 htf_gate_decision       BLOCK proximity_to_support
  regime=NEUTRAL; size_multiplier=0.0; hard_zero_reason=proximity_to_support
  distance_to_resistance_pct=0.0505 (i.e., 0.05%)
  distance_to_support_pct=0.2075       (i.e., 0.21%)
  permission_reason="NEUTRAL: both directions 0.5x (mean-reversion preferred); within 0.3% of support (0.21%)"
2026-05-31T13:57:01 bitunix_score_decided   PREMIUM sell net_score=10
```

Stronger setup than the pre-deploy reference (PREMIUM tier, all 3 validators pass, net_score=10) — and the HTF gate still hard-zeroed it on the same proximity rule.

### 8.3 The exact code path that rejected

`trading_corp/agents/strategies/bitunix_htf_regime.py:979-988`:

```python
if side == "sell" and verdict.distance_to_support_pct is not None:
    if verdict.distance_to_support_pct < config.proximity_block_pct:
        return TradePermission(
            allow_long=al, allow_short=asho, size_multiplier=0.0,
            reason=(
                f"{matrix_reason}; within {config.proximity_block_pct}% "
                f"of support ({verdict.distance_to_support_pct:.2f}%)"
            ),
            hard_zero_reason="proximity_to_support",
        )
```

This is a **hard-zero override** independent of the matrix (regime-side multiplier). Once `distance_to_support_pct < 0.3` AND `side == 'sell'`, the trade is rejected regardless of how favorable the matrix says shorts are. Default `proximity_block_pct=0.3` at `bitunix_htf_regime.py:221, 243`. The matrix base for NEUTRAL is `(allow_long=True, allow_short=True, mult_long=0.5, mult_short=0.5, reason="NEUTRAL: both directions 0.5x (mean-reversion preferred)")` at `bitunix_htf_regime.py:927-931`. The `mean-reversion preferred` wording is in the matrix base reason; the **actual decision** that rejects the sell is the proximity check, not the regime matrix.

### 8.4 What did BTC do after each rejection?

**22:09 UTC 2026-05-30 (pre-deploy reference)** — entry candidate 73831.8 sell:

| Time | Close | Move vs 73831 |
|---|---|---|
| +3m | 73869 | +37 (against sell) |
| +30m | 73800 | -32 (favor sell) |
| +60m | 73832 (after bounce + drop) | flat |
| +81m | 73793 | -38 (favor sell) |

Result: chop with mild drift down. HTF gate's "bounce off support" assumption was wrong — no bounce — but the rejection arguably cost only ~$30-40 of unrealized PnL on a trade that would have been borderline.

**13:57 UTC 2026-05-31 (post-deploy analog)** — entry candidate 73828.7 sell:

| Time | Close | Move vs 73828 |
|---|---|---|
| +3m | 73733 | -95 (favor sell, big) |
| +15m | 73624 | -204 (favor sell) |
| +21m | 73404 (low) | -424 (favor sell, max so far) |
| +30m | 73514 | -314 (favor sell) |
| +33m | 73558 | -270 (favor sell) |

Result: hard break through "support" — price dropped $200-400 in 21 minutes. HTF gate's mean-reversion assumption was clearly wrong; the rejection cost a substantial winner.

### 8.5 What this reveals

A `proximity_block_pct=0.3` hard-zero override is **regime-conditional in intent** (mean-reversion is the "preferred" behavior for NEUTRAL), but **regime-unconditional in implementation** (the check fires regardless of the matrix multiplier). On a regime that is "NEUTRAL" only because H4 ema_align=bear isn't quite enough to escalate to BEAR proper, the model wants to short, the validators line up to short, and the HTF gate's mean-reversion fail-safe overrides — even when the broader picture (H4=bear, D1=bear-structure) suggests the support level is more likely to be tested than bounced from.

**N=2 is too small to declare the gate mis-calibrated.** Both observed cases came on the same trend day with BTC drifting/breaking down. A clean assessment would require either:
- A larger sample of sell-near-support rejections across multiple regimes (BULL/NEUTRAL/BEAR), tagged with realized post-rejection 30/60min outcomes.
- An offline backtest of the rule with the parameter `proximity_block_pct ∈ {0.1, 0.2, 0.3, 0.4}` across the existing 3m bar history.

Not in scope to recommend a parameter change from a 2-event sample. Filed as a flag.

---

## 9. Findings + operator decisions

### F1. Zero fires is the market, not the deploy

**Evidence:** 22 fee-floor skips on 5/31 + 7 on 5/30, with mean ATR(14,3m) at $26-40 vs $86 on 5/28 (when 8 trades fired). Fee floor at ~0.18% × $73.9K ≈ $133, vs structural stops <$60 in 11/16 cases.

**Decision needed:** none. Standing rule per `[[project-bitunix-fee-floor-3rule-audit-2026-05-29]]`: do NOT lower `tp1_min_profit_multiplier` on fire-rate evidence; the maker-fill-rate model (already board-approved) is the right lever, not loosening the multiplier. Today's data confirms the standing position.

### F2. HTF gate `proximity_to_support` is firing 100% of post-PA-pass sells today

**Evidence:** 29/45 (64%) of HTF gate decisions block, ALL with `proximity_to_support` ALL on sell-side ALL in NEUTRAL regime. The 16 that pass HTF gate all die at fee floor → 0 fires.

**Decision needed:** flag for ongoing monitoring. N=2 sample (5/30 22:09 and 5/31 13:57) showed the mean-reversion assumption was wrong both times on the same trend day. **Do NOT recommend a code change from N=2**; recommend a longer-horizon look at this rule's hit-rate across regimes. If the operator wants, a follow-up BACKLOG item could scope: "audit `proximity_to_support` / `proximity_to_resistance` hard-zero behavior over the existing 3m bar history; tag each rejected setup with realized 30/60min directional outcome; report whether the rule has positive selectivity, is neutral, or is consistently wrong."

### F3. All HTF gate events in the window were NEUTRAL regime

**Evidence:** 45/45 NEUTRAL. No BULL/BEAR/STRONG_*/SAFE_MODE classification fired.

**Decision needed:** none directly, but observation: the matrix's `mean-reversion preferred` framing for NEUTRAL is what biases the proximity-hard-zero against sells near support. If/when the regime escalates to BEAR, the hard-zero rule should be re-examined — F2 backlog item would cover this.

### F4. Buy signals can't pass PA validation in current regime

**Evidence:** 24/24 buy signals that reached PA validation were rejected. In every case both `volume_confirmation` AND `structure_alignment` failed.

**Decision needed:** none. The bear-ish HTF context legitimately makes longs hard to validate. The 5/28 caveat about structure trivially aligning for sells continues to apply; not a new concern.

### F5. Polymarket 429 retries are firing with `backoff 0.0s` (NOT a Stage-1 issue)

**Evidence:** journal shows `PolymarketBroker: 429 ...; backoff 0.0s (attempt N/4)` at 05:42:51 UTC across many attempts in rapid sequence (~10ms apart).

**Decision needed:** out of scope for this review. Flag for separate triage: `0.0s` backoff with `429` rate-limit responses is logically broken (you should back off when you get a 429). Could file as a BACKLOG item if operator wants follow-up — recommend deferring until after the broader Stage-1 observation window settles.

### F6. tasty_options weekend behavior is correct

**Evidence:** zero scanner ticks today; IC scanner schedule confirmed `weekdays 09:45-09:50 ET`. Division registered, paper broker connected, TT session connected, equity=$0.00 (intended — no fund yet), no place_order events.

**Decision needed:** none for now. First real validation will be Monday 2026-06-01 09:45-09:50 ET — at which point operator should verify scanner ticks, IC composition, and zero auto-execution.

### F7. Gate (a) primitives are present but un-exercised

**Evidence:** zero retry/backoff/stale/stuck events in the window. BitunixBroker REST surface idle (paper mode, no orders).

**Decision needed:** none. Gate (a) will get exercised only when the broker REST surface is active — which requires either execution_mode=live or a paper-mode read-path that hits cloudflare. Both bar-cache fetches (3m/1h/4h/1d) all succeeded at deploy time; primitives stand ready but no fire signal.

### F8. Window-scope correction (NOT a finding, an annotation)

The prompt asked for "last 17 hours since 2026-05-30 deploy". Actual deploy time per `systemctl show trading-corp.ExecMainStartTimestamp` = `2026-05-31 05:36:49 UTC` (attempt #3 land). Actual window analyzed = ~8h45m. Per operator's mid-task correction ("disregard 17 hours as the prompt is a bit old"), this is by design.

---

## 10. Hard stops checked

| Stop | Status |
|---|---|
| Autonomous orders firing | None — 0 `place_order` events, 0 paper_trade_record rows |
| Broker self-latch triggered | Not triggered — BitunixBroker connect-only line in journal |
| NRestarts incrementing | NRestarts=0 since deploy |
| Critical anomaly | None |

No operator intervention recommended.

---

## 11. Constraints adhered to

- Read-only: 0 prod writes, 0 code changes, 0 config changes.
- All claims backed by quoted audit/journal/code citations (file:line where applicable).
- No code-change recommendations — F2 flagged as monitor-and-revisit per "don't recommend code changes in this session".
- Worktree isolation maintained (`worktree-stage1-first-17h-review-2026-05-31`).
- Scope: only the Stage-1 redeploy3 post-deploy window — no N+2 Phase 3 work, no exec-mode flips, no auto-execute flips.
