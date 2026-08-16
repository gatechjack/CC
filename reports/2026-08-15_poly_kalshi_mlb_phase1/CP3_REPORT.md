# CP3 — Guardrails (all five wired + proven to block)

**Status:** complete. **Dry-run only — zero real orders.** Holding at the CP3 gate; CP4 not started.
All 5 guardrails wired at the CP2-mapped insertion points; each proven to STOP an order (not just
flag). 16 executor tests pass. Shared files (`kalshi_copy_trader.py`, `sports_team_mapping.py`,
`kalshi_live.py`) byte-unchanged. Every claim below → paste or `file:line` in
`trading_corp/agents/strategies/poly_kalshi_executor.py`.

---

## 1. Wiring — gate order in `submit()` (`poly_kalshi_executor.py:227-263`)

```
[G-halt] :232   if self._is_halted(): -> blocked_halt            # FIRST, before any state change
[G-size] :235   if order.stake_usd > per_trade_cap: -> blocked_size_cap
[G-conf] :238   if order.confidence < 0.97: -> skip_below_threshold
[G-idem] :241   if key in self._placed: -> suppressed_duplicate   # READ only
[G-daily]:244   if self._deployed_usd + stake > daily_cap: -> blocked_daily_cap   # in-memory read
[G-slip] :249   if market_quote and _exceeds_slippage: -> blocked_slippage
                (live + no quote -> blocked_slippage_no_quote, fail-closed)
:257  # ALL GATES PASSED — commit exactly once:
:262   self._deployed_usd += stake      # counter incremented ONLY here
:263   self._placed[key] = order        # idempotency key burned ONLY here
```
- `[G-halt]` reuses the shared primitive — `_is_halted()` (`:210`) calls
  `StrategyState.from_persistence(strategy, db_url).halted`, the same `agent_state` row RiskAgent's
  daily-loss branch writes via `StrategyState.persist_halt`. Not reinvented.
- `[G-slip]` `_exceeds_slippage()` (`:218`): entry rejects when `yes_ask - base > cap`; exit when
  `base - yes_bid > cap`.

## 2. Per-guardrail block-proof (test → assertion → placed=0)

| guardrail | test | proves |
|---|---|---|
| `[G-size]` | `test_gsize_blocks_over_cap` | stake 6 > cap 5 → `blocked_size_cap`; `_deployed_usd==0`, `_placed==0` |
| `[G-daily]` | `test_gdaily_blocks_breach_and_counter_is_in_memory` | 2+2 ok, 3rd (→6>5) → `blocked_daily_cap`; counter stays **4.0** (breach not counted); counter is a plain `float` |
| `[G-slip]` | `test_gslip_blocks_thin_book` | thin book `yes_ask 0.70` vs base 0.55 (15c ≫ 2c) → `blocked_slippage`; healthy 1c book → would-place |
| `[G-halt]` | `test_ghalt_blocks_all_until_cleared_same_mechanism` | `persist_halt` → all submits `blocked_halt`; asserts `from_persistence(...).halted is True`; `clear_halt` → resumes |
| `[G-conf]` | `test_gconf_below_threshold_skipped` | conf 0.50 (e.g. doubleheader) → `skip_below_threshold`; `_placed==0` |
| `[G-idem]` | `test_gidem_replay_suppressed` | replay → `suppressed_duplicate`; `_placed==1` |

All 16 tests: `16 passed in 5.70s` (`-p no:pytest_ethereum`). No guardrail is "wired but unproven"
— each has a failing-path test above.

## 3. Order-of-operations proof (the one that matters)

- **Gate order** is fixed `[G-halt]→[G-size]→[G-conf]→[G-idem]→[G-daily]→[G-slip]` (`:232-249`),
  and **state mutates only after all gates pass** (`:262-263`).
- **Halt short-circuits before any counter increment or key burn** —
  `test_halt_short_circuits_before_counter_and_key`: with halt set, submit → `blocked_halt`,
  then `assert ex._deployed_usd == 0.0` **and** `assert o.idempotency_key not in ex._placed`.
- **A rejected order consumes no budget** — `test_rejected_order_does_not_increment_daily_counter`:
  a size-cap reject leaves `_deployed_usd == 0.0`.

## 4. In-memory daily counter (NOT an audit_event query) — code paste

```python
# __init__:
self._deployed_usd: float = 0.0        # [G-daily] running in-memory counter   (:207)
# submit(), [G-daily] gate:
# ...in-process float, NOT an audit_event aggregate query (that full-scan       (:244-246)
#    froze the engine; removed 2026-06-16).
if self._deployed_usd + order.stake_usd > self._daily_deployment_cap_usd:       # (:247)
    return self._record("blocked_daily_cap", order)
# commit (only after all gates pass):
self._deployed_usd += order.stake_usd   # [G-daily] counts only would-place     (:262)
```
There is no DB/`audit_event` read anywhere in the daily-cap path — it is a single in-process float.
`test_gdaily_...` asserts `isinstance(ex._deployed_usd, float)`.

## 5. Interaction checks

- **Counter counts only orders that PASSED all gates (would-place)** —
  `test_daily_counter_counts_only_would_place`: a below-threshold skip leaves the counter at 0.0;
  a passing order moves it to 2.0.
- **Dedup-suppressed replays don't double-count** —
  `test_dedup_replay_does_not_double_count_daily`: first submit → counter 2.0; replay →
  `suppressed_duplicate`, counter stays **2.0**.
- `[G-idem]` + `[G-conf]` still active after CP3 wiring (`test_gidem_replay_suppressed`,
  `test_gconf_below_threshold_skipped`).

## 6. Shared-files proof
`git diff --stat HEAD -- kalshi_copy_trader.py sports_team_mapping.py kalshi_live.py` → **empty**
(all three byte-unchanged). Re-verified at commit.

## 7. What is NOT done (gate discipline)
- No live orders, no live money (dry_run default; POST still gated).
- The **guardrail $ values are placeholders** (per-trade $5 / daily $20 / slippage 2c) — the real
  numbers are a CP5 operator gate. Wiring + block behavior is what's proven, not the thresholds.
- The **daily-loss halt DETECTION** side (RiskAgent computing realized-loss > threshold →
  `persist_halt`) is not exercised — it needs real fills; CP3 proves the halt **enforcement** gate
  reads the same primitive. Wiring RiskAgent's per-eval P&L check belongs to CP4's loop.
- `[G-slip]` live-quote fetch (+ fail-closed on fetch failure) is wired as a branch but the actual
  fetch is CP4 loop work; CP3 proves the guard rejects a thin book when a quote is present.
- No config / divisions / main.py wiring, no scheduled loop, no counter day-rollover (CP4).

## 8. Open for CP4
Detection loop (fast-poll + 429 backoff + capped whale set), day-rollover reset of `_deployed_usd`,
live-quote fetch feeding `[G-slip]`, RiskAgent P&L→halt detection wiring, config/main.py registration,
end-to-end shadow run. Carry-forward decision still open: exit-copy handling for hold-to-resolution
whales (CP2 §6).
