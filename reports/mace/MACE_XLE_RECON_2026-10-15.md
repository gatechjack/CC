# MACE XLE Stuck-Rung Diagnosis + DB Reconcile — Report (2026-10-15)

**Status: PART 1 DIAGNOSIS COMPLETE (read-only). PART 2 RECONCILE PREPARED — RESERVED for Jack's authorization.**
Nothing written to the prod DB. No restart, no arm/halt, no push. Engine/tree/DB untouched.
Branch `mace-xle-reconcile-2026-10-15` (worktree). Box engine PID 678426, re-read live.

All times UTC unless marked ET (ET = UTC−4, EDT). Stuck rung = `mace-XLE-2026-10-30-60-59-70-71-20260917`.

---

## TL;DR

1. **The "2 spreads" is BENIGN** — Jack's manual close method + RH display, **NOT** an engine split. MACE opens
   and closes a condor as ONE 4-leg combo; Jack closed it as FOUR separate single-leg `user` orders. The engine
   has **zero** partial/split audits for this rung. (Q2)
2. **The redesign is working as designed.** The stuck rung was correctly un-wedged (10/08 `closing_reopen`),
   repeatedly **deferred** (no latch, no loop), and **entered ride-to-expiry** (10/13 `ride_enter`,
   `natural_above_cap`). 6 real closes filled cleanly since deploy. The old unbounded "empty response" loop is gone. (Q1, Q3)
3. **★ NEW ANOMALY (bigger than the stuck rung): the box Robinhood SESSION has been failing since ~10/13 12:46 ET.**
   `"Login failed"`, `"...can only be called when logged in"` (820+), reauth `TimeoutError`. This froze the mark feed
   (mace_rung_live stuck at 10/13 16:46Z) and **is the true root of the "reconcile open orders error ×12."** Position
   management (PT/stop/time) has not run reliably for ~2 days. **This is RH-auth, not MACE code — and it is Jack's to fix
   (re-auth / restart). Booking the rung does NOT clear it.** (Q4, Q6)
4. **DB reconcile prepared** (guarded, idempotent, atomic backup). Recommended booking: `exit_debit=0.22`,
   `realized_pnl=+16.00`. RH's exact economic realized is **+$18.00** (not the ticket's est. +$20); the $2 gap is a
   convention artifact — Jack picks the booking. (Q7)

---

## PART 1 — DIAGNOSIS

### Q6 — Engine health
| Check | Result |
|---|---|
| PID / uptime | **678426**, up since **2026-10-08 16:38:24 UTC** (the stop-hotfix boot), `NRestarts=0`, active/running |
| /mace, /healthz | HTTP **200**, 200 |
| config_hash | **931a8214be50** ✓ (disk == /mace in-memory) |
| Code-intact (CR-md5) | strategy **b30db63f** / manager **dc04eb60** / domain **8522dc3a** / execution **9cc1c165** / config **d441957f** — **ALL == deployed targets** (box == redesign+hotfix bytes; also byte-verified against the local `mace-stop-hotfix-2026-10-08` tip) |
| LIVE RH (not paper) | `PaperExecutionBroker`=0, `MACE wiring FAILED`=0 since boot ✓ (MACE trades as `placed_agent=user` on Joint 116637293063 — it logs in as the user, not the agentic MCP) |
| Census | 18 open / 26 closed / 3 abandoned; **0 closing, 0 submitting** (the stuck rung is status=**open**, not wedged-closing) |
| Other stuck/wedged | none (0 closing/submitting; the one riding candidate — 57.5 rung — is a quietly-held dead wing) |
| Tracebacks since boot | **22** — all RH-session related (see Q4/anomaly), not MACE logic |
| Divisions | pmcc 639 / pead 38 / bitunix 2 error-ish since boot; PM `/positions` **429 storm** 452k (known, [[pm-zerocopy-latch-2026-10-06]]). None are MACE. |
| **Mark feed** | **★ FROZEN at 2026-10-13 16:46:50Z across ALL rungs** — see the anomaly below. |

### ★ The anomaly (root of Q4 + the mark freeze) — RH session failure
- `mace_rung_live` MAX(ts) = **2026-10-13T16:46:50Z**; every open rung's mark is stuck in the 10/13 16:35–16:46
  window → one loop-wide freeze, not per-rung staleness.
- `mace_manage_exits` last **2026-10-13 15:52Z**; `mace_mark_unavailable` 1316 (last 10/12 18:21).
- Journal (today): `429 ... get_prompts_status` → `Error during login verification: 'NoneType' object is not
  subscriptable` → **`Login failed. Check credentials and try again.`**; `17:29:54 RH in-process reauth did not
  complete (TimeoutError) — file likely stale, escalating`.
- `mace_reconcile_open_orders_error` distinct payloads since deploy: `"'NoneType' object is not subscriptable"`
  (145×) + `"get_all_open_option_orders can only be called when logged in"` (675×) → **820 total**; the ticket's
  "×12 @ 14:23 ET" is just the latest burst (12 on 10/15, 17:28–18:23Z, one per 5-min reconcile tick).
- Mechanism: when the shared RH session drops, quote fetch (→ marks), `open_orders()` (→ reconcile), and
  `open_positions()` all fail. Entries/snapshots still fire because they occasionally catch an authenticated
  window (SPY filled 10/15 19:46:43Z). **Net: no reliable live marks / position management since ~10/13 12:46 ET.**
- **Risk now:** LOW-and-bounded. All 18 open rungs are OTM defined-risk; nothing naked. But PT winners aren't
  being taken and a stop wouldn't fire. **Fix = re-authenticate the box RH session (Jack/restart) — reserved.**

### Q1 — Why MACE never closed the stuck rung (lifecycle trace)
The rung is a textbook **dead-wing uncloseable OTM winner**: the far-OTM long put (59) has no real bid, so the
condor marks as a garbage/inverted quote → every close attempt hits RH **"empty response"** (RH won't accept a
debit combo whose long wing has no market). Lifecycle:

- **Pre-redesign (10/02–10/07):** old loop — `manage_exit(pt)` → `exit_start` → `exit_error "empty response"` →
  `exit_exhausted` → `missed_exit`, every ~16 min (the exact failure the redesign targeted).
- **10/08 13:39 `mace_closing_reopen`** — redesign C1 un-wedged it from CLOSING → open. ✓
- **10/09–10/12:** PT/TIME fire → 5× `exit_rejected "empty response"` → **`exit_deferred`** (rung stays open,
  **no latch, no loop**). ✓ the reject-arm working.
- **10/13 13:39 `pt_mark_reject` (leg_inversion:** long put 0.08 ≥ short put 0.06) → **10/13 14:47
  `mace_ride_enter` gate=`natural_above_cap`** (natural 0.48 > cap 0.30) → **ride-to-expiry engaged**. ✓
- 10/13 15:01 `ride_cleared` (mark wiggled to 0.05, looked closeable) → PT → 5× rejected → deferred.
- Last engine event **10/13 15:02Z**; the mark feed froze at 16:46Z (RH session). Jack manually closed at 17:04–17:05Z.

**Verdict:** it was NOT wedged/looping (post-redesign). It was being correctly **held/ridden as an uncloseable OTM
winner** — the designed "HOLD-shaped" outcome. Jack simply chose to realize it manually rather than ride to 10/30.

### Q2 — The "2 spreads" verdict: **BENIGN (RH display + Jack's manual method), NOT an engine split**
Evidence, three independent sources:
- **Source:** `execution.close_rung` "Close a **whole condor**" → one `_place(spec, contracts, limit, combo_id=…-x{k})`
  on all 4 legs. No code path places a 2-leg order or acts on a leg subset. The closeability gate requires **both**
  long wings two-sided or it blocks → ride (never a partial close).
- **Engine audit (this rung):** `mace_exit_partial/entry_partial/drain_*` = **0 rows**. All 122 `exit_start`s were
  whole-condor `…-x{k}` combo attempts.
- **RH orders:** MACE **opened** it as ONE `iron_condor` order (`6aac438e`, 2026-09-17, 4 legs, one ticket) — same
  as today's SPY entry `6ad12da1`. Jack's **close** (10/13) was **FOUR separate single-leg `user` orders**
  (`placed_agent=user, agent_initiated=false`) 8s apart: 17:04:37 short_put, 17:04:45 long_put, 17:04:55
  short_call, 17:05:02 long_call.
- **Why it "looks like 2 spreads":** an iron condor = a put vertical (59/60) + a call vertical (70/71). RH's
  position view groups the 4 legs as those two verticals, and closing leg-by-leg/vertical-by-vertical (as Jack did)
  reinforces that. It is a **display + manual-method artifact**. The engine never split or half-closed anything.

### Q3 — Redesign health since 10/07–10/08 (real closes)
- **6 clean fills** (`mace_exit_fill`=6): XLE 59.5/58/70/71.5 PT **+$44**, XLE 60/58/69.5/71.5 PT **+$58**,
  SPY 730/727/791/794 TIME **−$12**, SPY 731/728/790/793 TIME **−$38**, SPY 746/743/803/806 TIME **+$40**,
  SPY 739/736/795/798 TIME **+$15**. Normal close path works on closeable condors (byte-parity ladder). ✓
- New paths engaged: `closing_reopen`=3, `ride_enter`=2, `ride_cleared`=2, `exit_deferred`=4, `exit_rejected`=70.
- **`mace_exit_error` stopped 10/07 19:40** — the old latching error path is dead; dead-wing rejects now route
  through the no-latch `exit_rejected`→defer/ride path. ✓
- **Hotfix target `XLE 57.5/56.5/67/68`:** 10/13 15:45 the loop fired a *legitimate* dead-wing stop (mark
  unpriceable + **short ITM**, spot 67.005 vs short call 67.0) → 5× rejected → 15:57 `closing_reopen` *"stop no
  longer valid on fresh quotes (spurious-stop self-heal)"*. Now status=open, mark=None, quietly held — **no spurious
  stop on a healthy OTM condor.** Hotfix working. ✓
- Caveat: "empty response" still APPEARS in rejects (the dead wing genuinely can't be closed) — but it no longer
  latches or loops. That is the intended behavior.

### Q4 — The "reconcile open orders error ×12"
It is an **RH-session/login failure**, not the stuck rung (see anomaly above). The 12 on 10/15 (17:28–18:23Z) are
`"get_all_open_option_orders can only be called when logged in"` (11×) + one `"'NoneType' object is not
subscriptable"`, one per 5-min reconcile tick. It is raised at `execution.reconcile` when `port.open_orders()`
throws — it takes no rung as input. **Booking the stuck rung will NOT clear it.** It clears when the box RH session
re-authenticates. (With 0 submitting rungs, the error is currently functionally harmless — the drain loop has
nothing to drain — but it is the visible symptom of the broader session failure.)

### Q5 — Today's eval (10/15, 15:46 ET) — HEALTHY
`SPY TAKEN` 741-738-802-805 x1 @ credit 0.94 (order `6ad12da1`, exp 2026-11-27) ✓. Skips all legitimate:
IBIT ivr 18.45<25, IWM ivr 22.32<25, XLE credit_floor 0.21<0.30, GDX credit_floor 0.83<1.05. **FXI**
`entry_unpriceable`×2 → `entry_standdown exhausted` = the known dead-wing thin-ETF skip ([[mace-entry-pricing-wing-asymmetry]]) — benign. Nothing off.

---

## PART 2 — DB RECONCILE (RESERVED; Jack authorizes + runs the write)

### Stuck rung (full row)
`mace-XLE-2026-10-30-60-59-70-71-20260917` — XLE, status **open**, exp 2026-10-30, legs sell60P/buy59P/sell70C/buy71C,
width 1.0, **contracts 2**, **credit_actual 0.30**, max_risk $140, entry 2026-09-17 (order `6aac438e`), pt_debit 0.15,
exit_*/realized = NULL, extra_json NULL. RH position for XLE 2026-10-30: **FLAT (nothing open)** — fully closed, nothing naked.

### P&L arithmetic (RH-verified)
- **Entry fill** (order `6aac438e`): net credit **$62** = sell60P 0.73 + sell70C 0.61 − buy59P 0.59 − buy71C 0.44 =
  **0.31/ct** (the order *limit* was 0.30 → DB `credit_actual=0.30`, $0.01/ct below the actual fill).
- **Close debit** (Jack's 4 fills, 10/13): (buy60P 0.08 + buy70C 0.50) − (sell59P 0.03 + sell71C 0.33) =
  0.58 − 0.36 = **0.22/ct = $44** total.
- **True economic realized = $62 − $44 = +$18.00** (= RH per-leg sum +$130 −$112 +$22 −$22). *(The ticket's +$20 used
  a rounded 0.74 short-put open; the actual fill was 0.73 → +$130, so the exact figure is +$18.)*
- **MACE-formula realized using DB credit_actual 0.30** = (0.30 − 0.22) × 100 × 2 = **+$16.00**.

### The booking — Jack's call on realized_pnl (a $2 convention fork)
| Option | SET | realized | Division PnL | Notes |
|---|---|---|---|---|
| **A (recommended)** | exit_debit=0.22, realized=**16.00**, credit_actual **unchanged** (0.30) | +$16.00 | $2 under RH | Formula-consistent with the DB's recorded entry credit — same convention as all 26 other closed rungs (credit_actual = order limit, systematically ≤ fill). |
| **B (exact RH)** | exit_debit=0.22, realized=**18.00**, credit_actual→**0.31** | +$18.00 | matches RH | Corrects the entry credit to the actual fill so the row stays formula-consistent (0.31−0.22=0.09→$18) AND Division PnL matches RH exactly. |

Common to both: `status='closed'`, `exit_ts='2026-10-13T17:05:02+00:00'` (last fill), `exit_reason='manual'`.
Runner defaults to **A**; to switch to **B**, change the two flagged lines (`REALIZED_PNL=18.00`,
`UPDATE_CREDIT_ACTUAL=0.31`).

### The guarded UPDATE (what the runner executes)
```sql
UPDATE mace_rung
   SET status='closed', exit_ts='2026-10-13T17:05:02+00:00', exit_reason='manual',
       exit_debit=0.22, realized_pnl=16.00
 WHERE rung_id='mace-XLE-2026-10-30-60-59-70-71-20260917' AND status!='closed';
```
Runner safety: atomic SQLite online-backup of the whole DB first (df-guarded), pre-image row saved to JSON,
idempotent (`AND status!='closed'` → no-op if already closed), asserts exactly 1 row updated else ROLLBACK, prints
PRE/POST. No broker calls.

### Will it clear the reconcile error / /mace open display?
- **/mace open display:** YES — the rung leaves the open set, stops showing open/active, and (once the RH session
  recovers) the manage loop will no longer attempt to re-close a flat position. ✓
- **"reconcile open orders error":** NO — that is the separate RH-session failure (Q4). It needs the box RH session
  re-authenticated (Jack / restart). Do not expect the booking to clear it.

### Runners (Jack runs, in order — reserved write)
```
powershell -ep bypass -f "C:\Users\AA Incorporado\cc\.claude\worktrees\mace-xle-reconcile-2026-10-15\mace_xle_reconcile_write.ps1"
powershell -ep bypass -f "C:\Users\AA Incorporado\cc\.claude\worktrees\mace-xle-reconcile-2026-10-15\mace_xle_reconcile_verify_ro.ps1"
```

---

## Read-only runs this session (autonomy-addendum RO; logged)
`mace_recon_health_ro`, `mace_recon_xle_ro`, `mace_recon_markloop_ro` (box, via sanctioned ssh-STDIN .ps1) +
Robinhood MCP RO: `get_accounts`, `get_option_orders` (×2), `get_option_positions`. No writes, no restart, no arm/halt.
