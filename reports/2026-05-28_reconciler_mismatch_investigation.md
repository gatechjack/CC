# Audit-reality reconciler mismatch — investigation (read-only)

**2026-05-28 ~23:00 UTC.** Dashboard surfaced `RECONCILER MISMATCH · 10/14` with three
v2 paper trades recorded `win` but the audit-reality reconciler reporting `still_open` +
`missed_legs ['tp1','tp2']`, plus a fourth `no_bars` case. These are post-PA-2of3 fires
used to declare the morning GREEN verdict. Read-only; no patches, no audit-row edits.

## VERDICT: Case 2 — the reconciler is WRONG. The 3 wins are REAL. GREEN holds.

The audit-reality reconciler cannot reproduce the live trade outcomes because it replays at
**3m** granularity while the live path resolved at **1m**, and `bitunix_bar_history` holds
**no 1m bars**. The flagged wins are genuine partial wins, confirmed by three independent
price sources.

## Per-trade ground truth

All three are SHORTs. TP hit ⇔ bar low ≤ TP price; moved-SL hit ⇔ bar high ≥ SL price.

### 28f43f1e (sell, entry 74828.4, SL 74987.11, tp1 74693.71, tp2 74669.69, tp3 74431.61)
- Recorded: win, R 0.9244, result_ts 2026-05-27T18:14:00.
- Real 1m bars (window 18:00→18:14): low **74655.6 @18:13**, **74636.0 @18:14** → ≤ tp1 AND tp2 → both hit. tp3 (74431.61) not hit. Then 18:14 H74724.8 / 18:15 H74715.6 ≥ moved-SL 74693.71 → close.
- 3m table bars (reconciler input): 18:03/06/09/12; the 18:12 bar L74636.0 (fills tp1+tp2) H74785.6 (≥ moved-SL) — but it is the **last bar in [entry,result_ts]**, so sim sees no next bar → `still_open`.
- position_sl_update: 18:13 tp1→breakeven(74828.4); 18:13 tp2→tp1-floor(74693.71). ✓
- **TP1 hit: YES. Win is real.**

### 0b118801 (sell, entry 74558.3, SL 74771.52, tp1 74424.10, tp2 74345.08, tp3 74025.24)
- Recorded: win, R 0.8146, result_ts 2026-05-27T22:16:00.
- Real 1m bars: low **74218.9 @22:15** → ≤ tp1 AND tp2. tp3 not hit.
- 3m table: single bar 22:15 L74218.9 (fills tp1+tp2) H**74466.5** ≥ moved-SL 74424.10 → close happens same-bar; sim has no next bar → `still_open`.
- position_sl_update: 22:13 tp1→breakeven; tp2→tp1-floor(74424.10). ✓
- **TP1 hit: YES. Win is real.**

### 6daca683 (sell, entry 74438.2, SL 74602.52, tp1 74304.21, tp2 74273.88, tp3 74027.40)
- Recorded: win, R 0.9076, result_ts 2026-05-28T01:18:00.
- Real 1m bars: low **74133.5 @01:17** → ≤ tp1 AND tp2. tp3 (74027.40) not hit.
- 3m table: single bar 01:18 L74111.5 (fills tp1+tp2) H**74365.8** ≥ moved-SL 74304.21 → same-bar close; no next bar → `still_open`. (Entry-containing 01:15:00 bar excluded by `ts_ms >= entry`.)
- position_sl_update: 01:17 tp1→breakeven; tp2→tp1-floor(74304.21). ✓
- **TP1 hit: YES. Win is real.**

### 99d62e04 (buy, entry 73231.8, SL 72990.03) — the no_bars case
- Recorded: loss, R -1.0, result_ts 2026-05-28T04:02:00.
- Reconciler window [04:00:02, 04:02:00] = 2 min. Containing 3m bar (04:00:00) excluded by `ts_ms >= entry_ts`; next 3m bar (04:03:00) > result_ts → **0 bars** → B7 guard refuses to declare a match (correct behavior).
- 1m 04:01 bar: L73110.1 (didn't reach SL 72990.03 in-window); SL hit just after 04:02. Loss plausible. Reconciler coverage gap, not a trade bug.

## Divergence: Case 2 (reconciler wrong). Mechanism + bug location

1. **Granularity mismatch (primary).** Live path resolves on **1m** (`paper_trade_replay.py:901`). Reconciler reads **3m** from `bitunix_bar_history` (`scripts/audit_reality_reconciler.py:80`). Table has only 3m/1h/4h/1d (6305×3m, **0×1m**). The reconciler cannot replay at the decision granularity.
2. **Moved-SL checked only on the next bar.** `paper_trade_replay.py:503` checks SL vs start-of-bar `current_sl`; `:562-574` fills TPs and moves SL; the moved SL is evaluated next iteration. When TP-fill + SL-bounce collapse into the **last 3m bar of [entry,result_ts]** (`:88-89`), there is no next bar → `still_open`. At 1m the bounce is a distinct in-window bar → correct close.
3. **`missed_legs` is a misnomer** (`audit_reality_reconciler.py:245` prints `sim_filled`). "missed_legs: ['tp1','tp2']" = legs the sim **filled**, not missed. Overstated the alarm.
4. **Window endpoint exclusivity** (`>= entry_ts`) drops the entry-containing 3m bar → contributes to `no_bars`/single-bar windows.

## Implications
- **Morning health-check GREEN HOLDS.** 6W/2L, avg R+0.41, "PA 2-of-3 firing as designed" — not invalidated; wins are real.
- **NEW YELLOW (the tool):** the audit-reality reconciler is broken for sub-3m-granularity v2 trades → false `still_open` + false `no_bars`. The "10/14 match" is misleading (the 4 non-matches are all granularity artifacts). Chronic false alarms risk masking a real future mismatch. Fix before trusting future reconciler output.

## Fix scope (NOT applied — operator decides)
- **Primary:** reconciler replays at **1m** — fetch 1m bars from the bitunix API (as the live path) OR persist 1m into `bitunix_bar_history` and query 1m.
- **Secondary:** extend window a few bars past `result_ts`; make it inclusive of the entry-containing bar (fixes `no_bars` endpoint exclusivity).
- **Cosmetic:** rename `missed_legs` → `sim_filled_legs`.
- No trade-data remediation needed; the `paper_trade_record` rows are correct.

## Methodology note
Real bars pulled live from `fapi.bitunix.com/api/v1/futures/market/kline` (public, no auth) and
cross-checked against the persisted `bitunix_bar_history` 3m table the reconciler reads. A wider
1m pull hit the bitunix 200-bar pagination quirk (returned an earlier slice) — not needed; the
close is confirmed by the 3m highs exceeding the ratcheted SL.
