# Kalshi copy-trading — MaggieTheEagle `mass_disappearance` alarm: ROOT-CAUSE REPORT

**Division:** kalshi_copy_trading  **Date:** 2026-07-29/30 (read-only investigation, no fix applied)
**Prod:** trading-corp.service, MainPID **450695** (engine py 450709), up since **2026-07-29 02:57:34 UTC**, NRestarts=0.
**Status:** Breaker firing every cycle and correctly containing. **Zero capital harm. Not urgent.**

---

## TL;DR
The alarm is **not a feed bug, not a billing issue, not a deploy regression, not a Maggie-profile problem.**
Two of MaggieTheEagle's three tracked Kalshi positions were **`KXFEDDECISION-26JUL-*` markets that
settled (finalized) at 17:59:00Z on 2026-07-29** when the July FOMC decision resolved. They legitimately
left her Kalshi *open-positions* page. The next scan saw 2 of 3 gone (66.7%), which trips the feed-health
`mass_disappearance` breaker (default threshold 60%). Because the breaker **retains the stale snapshot and
`continue`s**, it re-compares the frozen 3-position snapshot against the feed every cycle and **re-fires
forever** — a permanent latch. It will not self-clear.

**Root cause = real market settlement tripping a conservative feed-health heuristic that has (a) no
settlement awareness and (b) no recovery path for a *persistent* legitimate removal.**

---

## Evidence chain

**1. The anomaly (audit_event):** 46+ `kalshi_copy_feed_anomaly` events, 100% MaggieTheEagle, 100%
`reason=mass_disappearance`, identical payload `n_prev=3 / n_removed=2 / n_current=1 / pct=66.7`.
First **2026-07-29T18:09:53Z**, one per ~10-min cycle since. Zero non-Maggie anomalies.

**2. It is a SUCCEEDING-but-partial feed, not an error** — the code proves the branch:
`fetch_open_positions` raising → `_record_fetch_failure` (reason `consecutive_fetch_failures`).
The firing reason is `mass_disappearance`, which only runs on a **successful** fetch whose payload is
missing the whale's tickers. Journald confirms the actor succeeded every cycle:
```
17:49:44  apify open_positions[2 names]: 19 rows in 4828ms
17:59:48  apify open_positions[2 names]: 19 rows in 3632ms   <- last healthy; snapshot saved
18:09:53  apify open_positions[2 names]: 17 rows in 4473ms   <- -2 rows -> Maggie 3->1 -> breaker fires
```

**3. Feed is NOT down feed-wide.** The other selected whale **AI.EDGE is fully healthy** — snapshot
refreshed every cycle (updated_ts tracks the latest poll), 16 positions intact. The batched actor call
(`open_positions[2 names]` = Maggie + AI.EDGE) returns AI.EDGE fine and 1 of Maggie's 3. Rules out actor
failure / auth / rate-limit / code regression / whole-feed outage.

**4. Maggie is NOT private/delisted.** She still exposes 1 position every cycle (`n_current=1`). A private
or delisted profile would return 0.

**5. The two vanished markets SETTLED (Kalshi public API, $0 read-only GET):**
| ticker | status | result | close_time |
|---|---|---|---|
| KXFEDDECISION-26JUL-H0  | **finalized** | yes | **2026-07-29T17:59:00Z** |
| KXFEDDECISION-26JUL-H25 | **finalized** | no  | **2026-07-29T17:59:00Z** |
| KXFEDDECISION-26SEP-H0  | active        | -   | 2026-09-16T17:59:00Z |
Both July markets closed at **17:59:00Z** (the minute of the last healthy poll 17:59:48Z) and finalized;
by 18:09:53Z they were off the open-positions view. The still-active `-26SEP-H0` is the `n_current=1`
survivor. (July FOMC held rates: H0=yes, H25=no.)

**6. No deploy/restart involved.** Engine up continuously since 02:57:34Z (23h+), NRestarts=0 — ~15h
before the anomaly. NB: the brief's "PID 429030 / 12:11 UTC P1+P2" is **stale**; the live process is the
02:57 PMCC (e82a07d) deploy, MainPID 450695. The 12:11 change did not leave a running PID by that number,
and regardless the current process predates the anomaly by 15h. Feed-fetch path was not touched
(kalshi_apify_client.py unchanged; the row-count logs show the actor working normally right up to 17:59).

---

## The mechanism (why it repeats forever)

`kalshi_copy_trader.run_scan_cycle` (agents/strategies/kalshi_copy_trader.py):
- L336-341: on `_is_mass_disappearance(prev, removed)` → queue alarm → **`continue`** — which SKIPS
  `_save_whale_snapshot_raw`, so `agent_state positions:MaggieTheEagle` stays **frozen at the settled
  3-position state** (confirmed updated_ts = 2026-07-29T17:59:48Z, unchanged since).
- L845-858 `_is_mass_disappearance`: fires when `n_prev>=2 and removed>0 and removed/prev*100 >= 60`.
- No `feed_health:` block exists in prod strategies.yaml (verified) → **defaults** apply
  (mass_exit_threshold_pct=60, min_positions_for_check=2).
- Each subsequent cycle: prev = frozen {H0-26JUL, H25-26JUL, H0-26SEP}; feed = {H0-26SEP (+any new)};
  removed = the 2 finalized markets = permanently absent → 2/3 = 66.7% >= 60% → fires again, snapshot never
  advances. **Permanent latch. Will NOT self-clear** (no path resets the snapshot except operator action,
  cold-start via de-select/re-select, or a code fix).
- Side effect: the `continue` skips the ENTIRE whale, so while latched we are **not copy-trading Maggie at
  all** (new entries skipped too), not merely "suppressing exits". Modest functional cost.

---

## Impact — fully contained, no harm (STEP 4)

- We **never copied** any of the 3 Fed positions (snapshot our_side="" / copy_size_usd=0). Only historic
  KXFEDDECISION we ever copied is a June market (qty 3, resolved 2026-06-17). So even absent the breaker,
  the "exits" would be no-ops.
- Since 18:00Z: **0** `proposed_order`, **0** kalshi_copy_trading round-trips, **0** synthetic exits,
  **0** placed-live. Only unrelated event: one `kalshi_copy_entry_skipped_no_side` at 23:48 (a new-entry
  side-detect miss — normal).
- `auto_execute: true` (live since 2026-07-01) — irrelevant here since there is nothing to execute.
- Last-6h error scan across ALL actors shows only the anomaly kind; no tracebacks. Scanner healthy,
  polling on schedule, AI.EDGE processing normally.
- **Real costs of the current state:** (1) Telegram alarm spam, 1/cycle, indefinitely; (2) alarm-blindness
  — a genuine future Maggie feed problem would be lost in the noise; (3) Maggie de-monitored until cleared.

---

## Root-cause categorization
- Against the brief's taxonomy this is **NOT A** (actor fine), **NOT C** (not private — n_current=1),
  **NOT D** (no code touched, actor logs clean), **NOT the July billing incident** (budget fine).
- Closest to **B** (actor succeeds, returns partial) **but the cause of the partial is legitimate
  settlement**, not a Kalshi-side scrape defect. Net: a real upstream state change (settlement, ~E) x a
  **breaker design gap** (over-broad heuristic + permanent-latch retention) = **F (combination)**.

---

## Recommended fixes (ranked) — NOT applied this session

**R1 (proper fix) — Settlement awareness.** Before declaring `mass_disappearance`, check the removed
tickers' Kalshi market status (public API, $0). If a removed ticker is `finalized`/`closed`/`settled`,
it's a real exit → advance the snapshot (emit exits only if we held a copy) and DO NOT alarm. Only alarm
when removed tickers are still `active` (genuinely should still be in the feed). Precisely fixes THIS and
every future settlement day. ~1 cheap GET per removed ticker on anomaly cycles.

**R2 (general safety net) — Confirm-and-advance latch recovery.** If the same disappearance persists N
consecutive cycles (e.g. 3), treat as confirmed-real: advance snapshot, emit exits if held, and alarm at
most ONCE (not every cycle). Catches settlement, whale-close, and delisting without needing the API. Best
shipped WITH R1.

**R3 (noise band-aid, immediate) — Alarm de-dup/throttle.** Send the Telegram alarm once per
(whale, disappearance-signature) until it clears, instead of every cycle. Kills the spam now; does not fix
the latch or de-monitoring. Low effort.

**R4 (one-time operational clear, no deploy) — Reset Maggie's snapshot.** Rewrite
`agent_state positions:MaggieTheEagle` to the current feed reality (just the active -26SEP-H0) so prev==curr
and the latch clears immediately. Stops the spam without a code change, but **recurs next settlement day**.
This is a DB write → operator decision, out of scope for this read-only session.

**R5 (do NOT) — Raise mass_exit_threshold_pct.** Same class of mistake as "raise the cap": a whale holding
3 positions where 2 settle same-day (Fed days, expiry clusters) is normal; raising the threshold blinds the
breaker to the mass-exit case it exists to catch, and any low-position whale still trips it.

---

## Urgency
**Low — safe to leave firing while the fix is decided.** The breaker is containing correctly; no capital at
risk; nothing executes. If the operator wants the Telegram spam to stop *now* without a deploy, R4 (snapshot
reset) is the fastest one-time clear; otherwise the proper fix is R1+R2 next session. Recommend R1+R2.
