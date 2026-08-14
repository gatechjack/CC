# PEAD position card — design (Part 2), for approval before build

**Recommendation: (b) expand-on-click detail row.** Keep the compact book row exactly as
is (ticker + company name + the 4-cell exit-pressure strip + fuse bar); clicking a row
reveals a detail card *beneath it* — the "price ladder" + time countdown — like the SFP
division's card being its own panel.

**Why (b), not (a):** the compact pressure strip is the at-a-glance urgency read (STOP/DRIFT/
GUARD/TIME as normalized 0–100%) and is what makes a 25-name book scannable. Replacing it
(option a) would make every row tall and kill that. The card shows *different* information —
the **actual price geography** ($ levels + distances) and the **time countdown bar** — so it
is NOT a second copy of the pressure %s (satisfies the no-duplicate-viz constraint). Pressure
strip = "how close, relatively"; card = "to what price, and how far in dollars."

**All values are stored primitives (display only, no new computation):**
`entry_reference_price`, `stop_price`, `earnings_gap_top`, `pre_earnings_close`, `opened_ts`;
drift-dead = `pead_pressures.drift_dead_level` = `earnings_gap_top − 0.5×(earnings_gap_top −
pre_earnings_close)`; held = `business_days(opened, today)`; current = the row's live quote.
No exit logic is touched — the card only *reads* the same primitives the exit engine reads.

## Mockup — one real position (LRCX, live 2026-08-02)

```
┌ LRCX · Lam Research Corp.                    entry Jul 31 · SUE 3.69 · held 1/60d ┐
│ Entry $315.61   →   Now $293.01     −7.2%  ·  −$16.31                             │
│                                                                                  │
│         price ladder (entry ▸ stop, NOW marked)          distance from NOW       │
│  315.61 ●■ entry / gap-top                                                        │
│  293.01 ─────────◆ NOW ■■■■■■■■■■■■■■■■■■■■■■■■■■■■                                │
│  283.98 ⚠ drift-dead  (50% give-back = thesis invalid)   ▼ $9.03  (3.1%)  ← close │
│  252.35 · pre-earnings close                                                      │
│  250.50 ✖ stop  (2.5×ATR / swing-low)                    ▼ $42.51 (14.5%)         │
│                                                                                  │
│  TIME  ▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  1 / 60 trading d (59 left)│
└──────────────────────────────────────────────────────────────────────────────────┘
```

Markers are **price-proportional** on the entry→stop axis (range $65.11): NOW at 35% down,
drift-dead at 49%, pre-close at 97%. The read at a glance: LRCX is underwater and **sitting
just above its drift-dead thesis line** ($9.03), with the hard stop far below ($42.51) — a
picture the pressure strip's "DRIFT ~71%" implies but doesn't make concrete.

Green/amber/red on the NOW→drift-dead and NOW→stop gaps (reuse the fuse urgency thresholds)
so a position hugging its drift-dead line reads red.

## Implementation sketch (PEAD-only, display layer)
- `pead_view.assemble_book`: add `drift_dead` (from `drift_dead_level(prim)`), `pre_earnings_close`,
  and the ladder %-offsets to each complete row (all from existing primitives; None on
  incomplete rows). No new fetches.
- `partials/pead_live_sections.html`: wrap each book row's expand target in a click toggle
  (CSS `<details>`/checkbox or a tiny JS class toggle — no server round-trip; data already in
  the view), rendering the ladder + time bar in the revealed detail row. Compact row unchanged.
- Incomplete rows (no primitives) / gap≤0 rows (e.g. ADP, where earnings_gap_top < pre-close
  so drift is n/a): card shows entry/now/stop/time and marks drift-dead "n/a".
- No changes to `pead_pressures`, the sizer, the dial, or any exit path.
