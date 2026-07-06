# SFP Missed-Setup Forensic Review — 2026-07-06

**Operator override:** prior "working as designed / dormant is correct" read REJECTED.
Operator observed multiple profitable SFP setups (long & short) over ~48h+ that the
strategy did not trade. Read-only diagnosis. NO code changes, NO prod writes.

**Classification per setup:** (A) DETECTED-AND-GATED — detector fired, a filter/rule
refused; (B) NOT-DETECTED — detector never fired. A third state emerged and is called
out explicitly: (I) INCONCLUSIVE — evidence insufficient (short observability gap /
restart-timing).

All timestamps UTC. Operator gave EDT (UTC−4).

---

## Foundation (shared evidence, quoted from prod)

Prod DB `data/trading_corp.db`, detector `trading_corp/agents/strategies/bitunix_sfp.py`
(md5 `91fd76726364331c8083aaaa68fce199`, mtime **2026-06-28 19:52:37 UTC** — stable for
every setup except SOL 6/28 19:45, which ran the pre-19:52 build).

**F1 — Bar data is COMPLETE, no gaps** (rules out STEP-2a data-quality for all 8):
15m = 784 bars/coin (06-28 00:15→07-06 04:00), 3m = 3926 bars/coin. Every setup bar present.

**F2 — The detector arms ONLY on `pivotlow/high(50,50)`.** `_is_pivot_low` (bitunix_sfp.py:281):
`low[p]` strictly below all 50 bars each side → the extreme of **101 consecutive 15m bars
(~25h span)**, and it only CONFIRMS 50 bars (**12.5h**) after forming (`p = b - 50`, :201).
Fire (REAL long, `_maybe_fire` :296): `cur.low < swing_low AND cur.close > swing_low`
(sweep+reclaim); `close < swing_low` disarms; **permit is one-shot per arm**.
BOS (Mode-B 3m, `on_closed_3m_bar` :d): 3m close beyond the recent 3m two-candle swing,
within 240 3m bars (12h), not first closing back through the swept level.

**F3 — SHORT side only deployed 2026-07-01** ("bidirectional deploy", `reflect_neg` M2=0,
bitunix_sfp.py:160-176). Before 07-01, SFP was **long-only** — short setups pre-07-01 are
undetectable by construction.

**F4 — The detector's total fire-record over 06-27→07-06:**
- `sfp_watch_state` (longs/real stream): only **5 ARMs, all 06-28/06-30** — ETH 06-28 19:45
  (CONFIRMED→traded), SOL 06-28 22:45 (CONFIRMED), ETH 06-28 23:15 (CONFIRMED→traded),
  BTC 06-28 00:00 (TIMED_OUT), XRP 06-30 11:30 (TIMED_OUT). **Nothing after 06-30 11:30.**
- audit shorts: `sfp_skip_counter_trend` ×4 — BTC 07-03 14:03, XRP 07-03 11:03,
  ETH 07-04 13:48, ETH 07-05 22:48 (all `side=short, regime=up`).
- **OBSERVABILITY GAP:** short/reflected watch transitions are NOT persisted to
  `sfp_watch_state` (0 short rows despite 4 confirmed shorts). Only a short that
  BOS-confirms AND reaches the regime gate leaves an audit trail. Shorts that arm but
  do not confirm are invisible in the DB — this makes 2 setups below inconclusive.

**F5 — Regime side-gate** (bitunix_sfp_observer.py:675): `aligned = (regime in (up,range))
if side=='long' else (regime in (down,range))`; else `sfp_skip_counter_trend; return`.
Engine ran LIVE the whole window (`auto_execute=True, execution_mode=live, mode_b=True`).

**F6 — Arming timeline** (journal, retention back to 05-20): BTC/ETH always
`('3m','trading')`. **SOL/XRP wired into SFP only from the 07-02 02:10 restart**
(07-01 restarts show `symbols=['BTC','ETH']` only); SOL had a 06-28 fire, so SOL arm
history is non-linear (present 06-28 → removed ~07-01 → re-added 07-02 trading).

**Per-coin confirmed 50/50 pivots (the ONLY armable levels):**
- BTC: PL 06-29 02:00=58867, 07-01 01:00=57773, 07-05 09:30=62412 · PH 06-29 12:00=60758,
  07-02 14:15=62180, 07-03 21:00=62950, 07-04 19:30=63446
- ETH: PH 06-29 17:45=1637.17, 07-02 14:00=1723.95, 07-03 21:00=1775.16 · PL 06-30 13:30=1549.82, 07-05=1761.85
- SOL: PH 06-27 15:00=73.15, 06-28 12:30=72.38, 06-29 17:15=76.43, 07-02 11:15=82.77 · PL 06-28 05:30=70.11, 06-28 22:45=69.68, 06-30 13:00=71.87
- XRP: PH 07-02 13:45/14:00=1.1119, 07-04 17:45=1.1835 · PL 07-01 01:00=1.0374, 07-05=1.1246

---

## Per-setup traces

### Setup 1 — BTC 7/4 11:00 EDT = 2026-07-04 15:00 UTC
- **Armed levels @15:00:** PH 62950 (07-03 21:00, confirmed 07-04 09:30); PL 57773 (far).
- **15m setup bar 15:00:** O62671.9 **H63064.6** L62666.7 **C62804.0** → `H > 62950 AND C < 62950`
  = **short-SFP arm condition MET** (swept pivot-high 62950, reclaimed below).
- **3m after (15:15→16:30):** price chopped UP 62747→62970 (15:33 C62922, 15:45 C62970),
  never broke down; BTC then climbed to the 07-04 19:30 pivot high 63446. A short watch would
  **INVALIDATE** (close back above 62950) — no 3m BOS-down.
- **Fire record:** no `sfp_watch_state` row, no audit. Consistent with arm→invalidate (unpersisted).
- **VERDICT: (A) detected at 15m-arm → self-INVALIDATED by the detector's own rule (price
  reversed up).** Correct non-signal; a short here would have lost. If operator intended a LONG
  breakout, SFP targets reversals not continuations — no applicable signal. **Not a missed profitable SFP.**

### Setup 2 — BTC 7/3 09:45 EDT = 2026-07-03 13:45 UTC
- **Armed levels @13:45:** PH 62180 (07-02 14:15, confirmed 07-03 01:45); PL 57773 (far).
- **15m setup bar 13:45:** O62127.5 **H62259.5** L62025.0 **C62083.9** → `H > 62180 AND C < 62180`
  = **short-SFP fire MET** (swept pivot-high 62180, reclaimed).
- **Fire record:** audit `sfp_skip_counter_trend` **07-03 14:03** `{side:short, regime:up}`.
- **3m after:** 14:12 3m L61661.7 C61735 — BTC dropped ~500 pts (~0.8%) → the short worked.
- **VERDICT: (A) DETECTED + CONFIRMED + GATED by the regime side-gate** (short into up-regime).
  Gate refusal is per the validated design, **BUT this setup would have profited (~0.8%).**
  → Strategy-design conversation: the gate blocked a profitable counter-trend short.

### Setup 3 — BTC 7/2 09:15 EDT = 2026-07-02 13:15 UTC
- **Armed levels @13:15:** PL 57773 (07-01 01:00) — 3,900 below price; PH 60758 (06-29 12:00) —
  already broken (price closing ~900 above it). Next PH 62180 not confirmed until 07-03 01:45.
- **15m setup bars:** 13:15 O61653.7 H61954.7 L61631.2 C61668.2; 13:30 H62035.9. Price 61.6–62.0k,
  rising. 13:15 low 61631 > prior lows (no low swept); highs below the 07-02 14:15 forming pivot.
- **VERDICT: (B) NOT-DETECTED.** No `pivot(50,50)` at/near the swept level; the operator's swing is a
  minor/recent structure the 50/50 filter cannot arm on. Mechanism-vs-chart gap.

### Setup 4 — SOL 7/3 16:00 EDT = 2026-07-03 20:00 UTC
- **Armed levels @20:00:** PH 82.77 (07-02 11:15, confirmed 07-03 05:45); PL 71.87 (far). SOL wired trading since 07-02.
- **15m 20:30 bar:** H82.97 C82.75 → `H > 82.77 AND C < 82.77` **marginally MET** (reclaim by 0.02).
- **3m after:** poked 83.04 (21:12) then drifted to 82.13 (21:39) — a BOS-down was plausible but marginal.
- **Fire record:** no audit, no persisted short transition. SOL regime likely "range" (would ALLOW a short → would trade if confirmed), yet no trade.
- **VERDICT: (I) INCONCLUSIVE.** Marginal 15m arm; short transitions unpersisted (F4) → cannot confirm
  arm/BOS from the DB. Flag: observability gap + 0.02 reclaim margin.

### Setup 5 — SOL 6/29 13:15 EDT = 2026-06-29 17:15 UTC
- **Armed levels @17:15:** PH 72.38 (06-28 12:30, confirmed 06-29 01:00) — 3+ below (broken);
  PL 69.68 (far). The 17:15 bar (H76.43) is ITSELF the forming pivot high — confirms 12.5h later, unarmable now.
- **15m setup bar 17:15:** O75.25 H76.43 L75.23 C75.9. Price 75–76.4, far above every armed level.
- **VERDICT: (B) NOT-DETECTED.** No armed `pivot(50,50)` at that level; the swing being traded is the
  live-forming pivot (not yet confirmable). (Also: short engine not deployed until 07-01 if a short read.)

### Setup 6 — ETH 7/1 17:45 EDT = 2026-07-01 21:45 UTC
- **Armed levels @21:45:** PH 1637.17 (06-29 17:45, confirmed 06-30); PL 1549.82.
- **15m setup bar 21:45:** O1626.99 **H1637.88** L1626.18 **C1631.33** → `H > 1637.17 AND C < 1637.17`
  = **short-SFP fire condition MET** (swept pivot-high 1637.17, reclaimed).
- **3m after (22:00→23:15):** ETH FELL 1631→**1614.78** (~1%) — a BOS-down was structurally available.
- **Fire record:** NO detector event of any kind for ETH 07-01.
- **Timing:** short engine "bidirectional deploy" was 07-01, but the exact go-live restart on 07-01
  (restarts 03:05/03:23/03:42/14:09/19:35/22:52) is undetermined; warm-start of the reflected engine
  must have rebuilt the 1637.17 arm.
- **VERDICT: (I) INCONCLUSIVE — MOST CONCERNING.** Either the short engine was not yet live/warm at
  21:45 (deployed at a later 07-01 restart) OR a genuine missed short (fire condition met + follow-through
  present + no event). Cannot resolve from the DB (short transitions unpersisted, F4). **Flag for follow-up:
  pin the exact short go-live restart on 07-01.**

### Setup 7 — SOL 6/28 15:45 EDT = 2026-06-28 19:45 UTC
- **Armed levels @19:45:** PL 70.11 (06-28 05:30, confirmed 06-28 18:00); PH 73.15 (far). (pre-19:52 build.)
- **15m setup bar 19:45:** O70.76 H70.76 **L70.21** C70.69 → `low 70.21 < swing_low 70.11`? **NO**
  (70.21 > 70.11 by 0.10) → sweep condition FALSE → no fire.
- **Corroboration:** the detector DID fire the SAME 70.11 pivot at **06-28 22:45** (`sfp_watch_state`
  SOL REAL CONFIRMED, swept_level 70.11) when price actually dipped below it (22:45 bar low 69.68).
- **VERDICT: (B) NOT-DETECTED at 19:45 — the sweep threshold was not met (low 70.21 did not break
  pivot 70.11).** The operator anticipated a sweep that occurred 3h later (22:45), which the detector DID catch.

### Setup 8 — XRP 7/3 08:00 EDT = 2026-07-03 12:00 UTC
- **Armed levels @12:00:** PH 1.1119 (07-02 13:45/14:00, confirmed 07-03 ~02:15); PL 1.0374. XRP wired trading since 07-02.
- **15m setup bar 12:00:** O1.1095 **H1.1136** L1.1086 **C1.109** → swept pivot-high 1.1119, reclaimed.
- **BUT** the reflected permit is **one-shot** and was already consumed at **07-03 11:03**
  (audit `sfp_skip_counter_trend` XRP `{side:short, regime:up}`) on the SAME 1.1119 pivot.
- **3m after 12:00:** XRP dipped 1.109→1.1032 (12:30) then recovered to 1.1134 (13:30).
- **VERDICT: (A) fired at 11:03 → GATED counter_trend** (short into up-regime). The 12:00 re-test is the
  same level; the detector correctly did NOT re-fire (one-shot permit). Gate per-design; outcome ~marginal.

---

## Aggregate

| # | Setup (UTC) | Bucket | One-line |
|---|---|---|---|
| 1 | BTC 07-04 15:00 | A | swept PH 62950 → self-invalidated (price rose); short would've lost |
| 2 | BTC 07-03 13:45 | **A** | swept PH 62180 → CONFIRMED short → **GATED counter_trend; would've profited ~0.8%** |
| 3 | BTC 07-02 13:15 | **B** | price far from any 50/50 pivot; minor swing not armable |
| 4 | SOL 07-03 20:00 | I | marginal graze of PH 82.77; short unpersisted → inconclusive |
| 5 | SOL 06-29 17:15 | **B** | price far above armed pivots; swing is the live-forming pivot |
| 6 | ETH 07-01 21:45 | **I** | swept PH 1637.17 + fell 1% + NO event → **most concerning; short-engine timing** |
| 7 | SOL 06-28 19:45 | **B** | low 70.21 did not break pivot 70.11; same pivot fired 3h later |
| 8 | XRP 07-03 12:00 | A | fired 11:03 → GATED counter_trend; 12:00 = one-shot re-test (correct no re-fire) |

**Counts:** A = 3 (BTC 7/4, BTC 7/3, XRP 7/3) · B = 3 (BTC 7/2, SOL 6/29, SOL 6/28) · I = 2 (SOL 7/3, ETH 7/1).

**Common failure mode — Bucket B (3):** the detector's `pivot(50,50)` anchoring (extreme of 101
15m bars, 12.5h confirmation lag) does not recognize the shorter-term / recently-formed swings the
operator trades. Price was simply not sweeping any armed major pivot. This is a genuine
**mechanism-vs-chart disagreement**, not a bug in the pivot code — the code does exactly what it says;
it just keys on far larger structure than the operator's chart read.

**Bucket A (3):** all three are the detector working. Two (BTC 7/3, XRP 7/3) are **profitable/near-profitable
counter-trend SHORTS refused by the regime side-gate** (short into up-regime). This is the validated-design
vs operator's-read conversation: the gate is doing what Piece-3 specified, but it declined trades the operator
judged good. **Flag, do not decide.** One (BTC 7/4) is the detector correctly self-invalidating a failed short.

**Bucket I (2) — the two that need more:** short/reflected watch transitions are **not persisted**
(F4), so arm-but-unconfirmed shorts cannot be audited from the DB. ETH 7/1 is the most concerning:
15m fire condition met + 1% follow-through + zero detector event — resolvable only by pinning the
exact 07-01 short-engine go-live restart (and, ideally, adding short-watch persistence so future
short misses are auditable).

**Recommended next steps (not decisions):**
1. Pin the exact 07-01 restart that brought the reflected/short engine live+warm; re-trace ETH 7/1.
2. Add persistence for short/reflected watch transitions (close the F4 observability gap) so short
   misses become auditable like longs.
3. Strategy-design review of the regime side-gate vs operator's counter-trend reads (BTC 7/3, XRP 7/3).
4. Strategy-design review of `pivot(50,50)` sensitivity vs the swings the operator actually trades
   (the Bucket-B root cause).
