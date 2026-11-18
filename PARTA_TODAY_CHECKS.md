# Part A — Today's checks (2026-11-17), read-only

Probed 2026-11-18T01:25:16Z via RO SSH (sqlite3 -readonly + journalctl + ps only; raw output in
`probe_artifacts/partA_probe_output.txt`). Market-side reconstruction via the claude.ai read-only
Robinhood MCP connector (separate session; cannot touch the box session). No writes, no logins,
no prod mutations. Worktree base 13725707 == running box files (9/9 CR-stripped md5 match,
`probe_artifacts/probe1_output.txt` section [7]).

**No STOP-AND-ALERT condition is met**: session authenticated now (zero auth/reconcile errors since
the 21:08Z restart), no rung unpriced >2 consecutive ticks observed, no open short ITM, no open rung
at its stop.

---

## A.1 The SPY-732 stop at 11:23 ET — VERDICT

**A false stop from bad inputs. The rule as written executed correctly, but its "dead wing" premise
was manufactured by a transient 30-second quote timeout on the 729 LONG PUT (3 points OTM — a liquid
strike), not by an unquoted far-OTM wing; the short put was 1 cent ITM; and the plain 2x-mark stop
(level 1.86) would not have fired then or at any point in the following hour (condor ~1.10-1.20).**

### The rung

| field | value |
|---|---|
| rung_id | mace-SPY-2026-12-18-732-729-798-801-20261104 |
| structure | short 732P / long 729P + short 798C / long 801C, exp 2026-12-18 (31 DTE at exit) |
| contracts | 1 | 
| credit_actual | 0.93 (entry 2026-11-04T20:46:54Z, max_risk $207, entry ATM IV 0.165) |
| 2x stop level | mark >= 1.86 |
| exit | stop, decision 16:23:47Z, filled 16:25:46Z @ 1.32 debit, realized **-$39.00** (-42% of credit) |

### Tick-by-tick 11:00 ET -> close order

Engine-side per-tick marks are NOT persisted (`mace_rung_live` is INSERT OR REPLACE —
execution.py:321; hold-ticks write no audit row), so engine ticks between 16:00Z and 16:23Z are not
reconstructable → per-leg engine bid/ask per tick: **not determined**. Market-side (RH historicals;
option bars are TRADE prices, so spread math is approximate):

| UTC (ET) | SPY | 732P | 729P | P-spread | 798C | 801C | C-spread | condor ~ |
|---|---|---|---|---|---|---|---|---|
| 16:00 (11:00) | 731.35→732.21 | 15.98 | 14.92 | 1.06 | 0.24 | 0.20 | 0.04 | 1.10 |
| 16:10 (11:10) | 733.10→732.79 | 16.80 | 15.69 | 1.11 | 0.27 | 0.22 | 0.05 | 1.16 |
| 16:20 (11:20) | 732.02→731.50 (low 731.01) | 17.26 | 16.14 | 1.12 | 0.26 | 0.21 | 0.05 | 1.17 |
| **16:23:47** | **spot used: 731.99** | — | **leg fetch TIMED OUT (30s)** | — | traded all hour | traded all hour | — | mark=None |
| 16:30 (11:30) | 731.29→730.77 | 18.03 | 16.87 | 1.16 | 0.26 | 0.21 | 0.05 | 1.21 |
| 16:50 (11:50) | 729.43→728.87 (day low 728.21) | 19.35 | 18.18 | 1.17 | 0.27 | 0.21 | 0.06 | 1.23 |
| 17:10 (12:10) | 731.61→731.76 | 15.47 | 15.05 | 0.42* | 0.30 | 0.21 | 0.09 | (sparse trades) |
| 17:30 (12:30) | 733.29→732.56 | 17.29 | 16.20 | 1.09 | 0.29 | 0.23 | 0.06 | 1.15 |

*17:10 is a sparse-trade artifact (unsynchronized last-trades), not a real spread collapse.

### What actually happened at 16:23:47Z (all three events in the same second)

1. `mace_leg_quote_timeout {"symbol":"SPY","opt_type":"put","strike":729.0,"timeout_sec":30}` —
   the 4-leg snapshot's 729P fetch hit the full 30s `leg_quote_timeout_sec` (execution.py:607-621)
   → that leg None → condor mark None.
2. `stop_triggered` (strategy.py:817-839): mark None → mark test skipped → structural fallback
   (strategy.py:836-838): `spot 731.99 <= short_put 732.0` → True. **Branch fired: the dead-wing
   structural short-ITM fallback, on a 1-cent ITM short put.**
3. `mace_manage_exit` reason=stop, detail `dead-wing mark unpriceable + short ITM (spot 731.99 vs
   732.0/798.0)`; `mace_exit_start` pricing=marketable (natural-floored: buy shorts @ask, sell wings
   @bid-or-0), trigger_mid=None.

### Was the wing genuinely unquoted, or a quote timeout?

**A quote timeout.** The leg that failed was the 729 long put — 3.0 points OTM with SPY at 731.99,
one of the most liquid strikes on the board (it traded continuously: 15.46-18.33 across the hour).
The genuinely illiquid far wings (798C/801C, 66-69 points OTM) priced and TRADED all hour (0.19-0.31).
This is the only `mace_leg_quote_timeout` in the entire day. The "dead wing" the fallback assumes
(far-OTM long with no bid) did not exist; the mark was unpriceable only because one liquid leg's
fetch timed out once.

### Close order and aftermath

- Placed 16:23:47Z (marketable ladder), filled 16:25:46Z @ **1.32** vs concurrent trade-price mid
  ~1.17 — roughly $15 of the $39 loss was ladder-crossing cost.
- Next hour: SPY fell further to 728.21 (16:45-16:50Z, 3.8 pts through the short strike), then
  rallied to 734.40 (17:15-17:20Z), ~732.6 at 17:30Z; engine's final spot 729.845 (20:52-20:57Z).
- The condor's value stayed ~1.10-1.23 throughout — **the 2x-mark stop (1.86) was never approached**,
  even with the short put 3+ points ITM. The structural test and the economic test disagreed by ~35%
  all afternoon.

### Consequence for Part B

The spot>0 guard (the 11-16 fix) would NOT have prevented today's stop — spot was valid (731.99).
Today is a SECOND distinct bad-input mode: transient leg-timeout -> mark=None -> structural fallback
with no materiality/persistence requirement. Per the task's conditional ("propose, do not build,
unless A.1 shows a false stop" — it does), Part B also builds a one-tick CONFIRMATION for the
structural dead-wing stop: it fires only when the (mark unpriceable + short ITM) condition has held
on two consecutive ticks. A transient timeout self-heals next tick (mark prices again → the 2x test
resumes); a genuine dead-wing ITM breach persists and exits 5 minutes later. The 11-05 XLE real stop
(0.23 ITM, dead wing) would have fired one tick later; the 10-30 GDX gap stop (2.68 ITM) likewise.
A materiality (minimum-ITM) floor was considered and REJECTED: the legitimate 11-05 XLE stop was
only 0.36% ITM, so any floor big enough to stop today's false fire would also have suppressed a
stop Jack treats as correct.

---

## A.2 The 16:29Z manage timeout / 16:30Z stall

- **Same tick as the SPY close.** `manage_tick_timeout_sec=600` (config.py:125, default; cancel at
  16:29:35Z ⇒ tick started ~16:19:35Z). Inside that one tick: SPY-732's snapshot began ~16:23:17,
  burned the full 30s leg timeout (16:23:47), the stop decision + marketable ladder ran 16:23:47 →
  fill 16:25:46 (~2.0 min), the sweep continued over the remaining rungs and was cancelled mid-sweep
  at 16:29:35. The exact rung/call in flight at the cancel instant: **not determined** (the adjacent
  journal lines are binary-blob entries from other divisions).
- The 30s timeout + 2.5 min of exit work consumed a quarter of the 600s budget; elevated quote
  latency across the other 12 rungs did the rest.
- **Not an isolated event — 3 cancelled ticks today**: 16:29:35Z, 16:44:35Z (the very next tick,
  started 16:34:35Z — no exits in it, so quote latency alone overran 600s), 19:37:53Z. Stall alerts
  (watchdog, 900s): 16:30:20Z (tick_age 946s) and 19:38:23Z (931s) — each because the cancelled
  tick(s) never advanced the heartbeat. All self-healed on the next completed tick.
- **Priced-rung count per completed tick: not determined** — not persisted anywhere (heartbeat and
  mace_rung_live are last-state-only). Proxies: exactly ONE leg-quote timeout all day (the 729P);
  zero `mace_mark_unavailable` and zero `mace_marks_dead` today; final completed tick 20:57:44Z
  heartbeat = open_count 12, marked_ok **12/12**. During the two consecutive cancelled ticks
  (16:19:35 + 16:34:35 starts) rungs after each cancellation point went unevaluated — at most 2
  consecutive ticks, below the >2-consecutive alert threshold.

## A.3 Snapshot, entry eval, day, census

- **15:40 snapshot** (20:40:06Z, old PID 809222): equity **$279.34**, available BP $558.68
  (deployment basis available_buying_power). Re-ran 21:09:03Z after the 21:08Z restart (PM v2chase
  deploy — new PID 991148), same values; that explains the "irregular 21:09" snapshot.
- **15:45 entry eval** (20:45:59Z): IBIT SKIP cooldown (1 session since stop); XLE SKIP credit 0.50
  < floor 0.60; GDX SKIP credit 1.33 < floor 1.50; FXI SKIP credit 0.24 < floor 0.30; SPY SKIP
  cooldown (0 sessions — today's stop); IWM ENTER decision (12-31 250/247-286/289 x2 @ ~1.02) →
  `mace_entry_rejected` → `mace_entry_standdown` (attempts=0, reason=rejected). **0 entries filled.**
- **Day**: 1 exit (the SPY stop, -$39), 0 entries, day realized **-$39.00**. Daily summary 20:50:03Z:
  equity 279.34, open 12, day_pnl -39, **breakers active: day_loss + week_loss**.
- **Census now**: 12 open (7 SPY, 3 IWM, 2 GDX), 51 closed, 3 abandoned (old operator-cleared
  phantoms). Last tick (20:52-20:57Z) marks vs 2x stop, spots vs shorts:

| rung | mark | 2x stop | % of stop | nearest short vs spot |
|---|---|---|---|---|
| GDX 76/74-99/101 12-18 x2 | 0.665 | 1.30 | 51% | put 76 vs 79.35 (OTM 3.35) |
| GDX 78/77-102/103 12-18 x2 | 0.550 | 0.62 | **89% — watch** | put 78 vs 79.35 (OTM 1.35) |
| IWM 262/259-297/300 12-18 x2 | 0.895 | 1.88 | 48% | put 262 vs 268.775 |
| IWM 250/247-289/292 12-18 x1 | 0.670 | 1.92 | 35% | put 250 vs 268.775 |
| IWM 263/260-293/296 12-24 x1 | 1.080 | 1.82 | 59% | put 263 vs 268.775 |
| SPY 715/710-787/792 12-11 x2 | 1.325 | 3.22 | 41% | put 715 vs 729.845 |
| SPY 715/710-786/791 12-11 x2 | 1.400 | 3.18 | 44% | put 715 vs 729.845 |
| SPY 713/710-790/793 12-18 x2 | 0.930 | 1.90 | 49% | put 713 vs 729.845 |
| SPY 685/680-773/778 12-18 x1 | 1.320 | 3.32 | 40% | put 685 vs 729.845 |
| SPY 685/680-773/778 12-18 x1 | 1.260 | 3.22 | 39% | put 685 vs 729.845 |
| SPY 725/720-790/795 12-24 x1 | 1.830 | 3.02 | 61% | put 725 vs 729.845 |
| SPY 729/726-782/785 12-24 x2 | 1.315 | 2.40 | 55% | put 729 vs 729.845 (**OTM by 0.85**) |

  No short ITM; no rung at stop. Closest risks: GDX-78 at 89% of its stop level, and the SPY 12-24
  729 short put 0.85 above spot.
- **Session now**: engine PID 991148 up since 21:08:01Z (LIVE, env-authorized); boot loaded the
  stored session and validated it (no challenge; pickle mtime unchanged at 01:40:38Z); last auth
  event remains `rh_auth_recovered` 01:40:48Z; today's 20 reconcile errors were all 00:xx-01:xxZ
  (pre-recovery); **0 MACE error/fail events since 21:00Z**.
