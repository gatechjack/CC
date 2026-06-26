# SFP watch-state emit + loop heartbeat — READ-ONLY SCOPE (review before build)

Date 2026-06-26. Target: light up the cockpit's Tier-B panels (armed-watch card + countdown,
near-miss, BOS-confirm rate, swept/BOS overlays) by persisting the watch lifecycle the observer
already computes. **bitunix_sfp is LIVE + ARMED (PID 3633090, observer `db831daf`).** Nothing
in this doc is built or deployed — scope only.

## TL;DR
- **All four lifecycle transitions are computed inside `SfpDetector` (`bitunix_sfp.py`), NOT the
  observer.** The observer only ever sees CONFIRMED (the yielded `SfpEntrySignal`); ARMED,
  INVALIDATED, TIMED_OUT are decided internally and **silently dropped**.
- To emit them observe-only, the detector must SURFACE them. Recommended: an **additive,
  write-only transition buffer** the observer drains after `on_closed_bar()` and persists. The
  returned signal list is byte-identical → decision path unchanged → parity holds.
- Persist to a **new `sfp_watch_state` table** (UPSERT on a stable `watch_id`, one row per watch
  updated ARMED→terminal). Heartbeat → **`agent_state`** (no migration).
- **Two files change: `bitunix_sfp.py` (detector) + `bitunix_sfp_observer.py` (observer).**
  `bitunix.py` (broker) is NOT touched. Restart required (observer/detector code).

---

## Q1 — Where each transition is evaluated (file:line)

All in `trading_corp/agents/strategies/bitunix_sfp.py`, inside `SfpDetector.on_closed_bar`
(L158) and its helpers:

| transition | file:line | what happens |
|---|---|---|
| **ARMED** (sweep → watch) | `bitunix_sfp.py:186-191` | `_maybe_fire(b)` returns the swept wick low; `self._watches.append(_Watch(fire_index=b, level=self._swing_low, swept_low=fired_swept))` |
| **INVALIDATED** (closed back below swept level) | `bitunix_sfp.py:263-264` (`_advance_watch`) | `if cur.close < w.level: return "invalid"` → watch dropped at L204 |
| **TIMED_OUT** (watch window expired) | `bitunix_sfp.py:260-261` (`_advance_watch`) | `if (b - w.fire_index) > self.watch_bars: return "timeout"` → watch dropped |
| **CONFIRMED** (BOS → entry) | `bitunix_sfp.py:266-276` (`_advance_watch`) | returns `SfpEntrySignal`; bubbles up as the return of `on_closed_bar` |

The observer side: `bitunix_sfp_observer.py:206-214` `_process_symbol` feeds bars
(`det.on_closed_bar(bar)`) and only handles the **returned signals** (`_handle_signal`, L213) =
the CONFIRMED path. It has **no visibility** into ARMED/INVALIDATED/TIMED_OUT today.

## Q2 — Data already in-hand at each transition (persist what's computed; recompute nothing)

| field | source at transition |
|---|---|
| `mode` (REAL/CONSIDERABLE) | `det.mode` (per-detector) |
| `symbol` | observer side — `wire` in `_process_symbol` (the detector is symbol-agnostic) |
| `fired_bar_ts` | `self.bars[fire_index].ts_ms` (the arming bar) |
| `swept_level` (invalidation line) | `_Watch.level` (= the swept pivot-low) |
| `swept_wick` | `_Watch.swept_low` (the wick low that swept) |
| `bos_watch_level` | `_most_recent_swing_high(before_index=b)` — the two-candle swing high a BOS must close above. **Nuance:** this is dynamic per bar (re-derived each advance). Capturable at ARM (current candidate) and finalized at CONFIRM (`SfpEntrySignal.bos_ref_high`). |
| `status_ts` / terminal bar | the current bar's `ts_ms` at the resolving transition |

Everything except `symbol` is local to the detector; `symbol` is attached by the observer. **No
recomputation** — the emit logs values already produced.

## Q3 — Where the record lives (recommendation + schema)

**Recommend a new table `sfp_watch_state`** (UPSERT, one logical row per watch). Rationale: the
dashboard wants "one record per watch, updated ARMED→terminal" + near-miss (terminal rows) +
BOS-confirm-rate (CONFIRMED / total ARMED). That's an upsert-by-id with a `status` column —
awkward against append-only `audit_event` (would need event-reduction on read). A dedicated
table queries cleanly and is observe-only.

```sql
CREATE TABLE IF NOT EXISTS sfp_watch_state (
    watch_id           TEXT PRIMARY KEY,   -- stable: f"{symbol}:{mode}:{fired_bar_ts_ms}"
    symbol             TEXT NOT NULL,      -- wire symbol, e.g. BTCUSDT
    mode               TEXT NOT NULL,      -- REAL | CONSIDERABLE
    fired_bar_ts_ms    INTEGER NOT NULL,   -- ts_ms of the arming bar
    swept_level        REAL NOT NULL,      -- swept pivot-low (invalidation line)
    swept_wick         REAL NOT NULL,      -- wick low that swept it
    bos_watch_level    REAL,               -- swing-high BOS target (arm-time; -> bos_ref at CONFIRM)
    status             TEXT NOT NULL,      -- ARMED | CONFIRMED | INVALIDATED | TIMED_OUT
    status_ts          TEXT NOT NULL,      -- ISO-8601 UTC of the latest transition
    armed_ts           TEXT NOT NULL,      -- ISO-8601 UTC when armed
    terminal_bar_ts_ms INTEGER,            -- resolving bar ts_ms (NULL while ARMED)
    extra_json         TEXT                -- entry_bar_index / bos_ref_high on CONFIRMED
);
CREATE INDEX IF NOT EXISTS ix_sfp_watch_state_status ON sfp_watch_state(status, status_ts);
CREATE INDEX IF NOT EXISTS ix_sfp_watch_state_symbol ON sfp_watch_state(symbol, status);
```

`watch_id = f"{symbol}:{mode}:{fired_bar_ts_ms}"` — unique (permit-once per symbol+mode+bar) and
**deterministic across restart**, so the warm-start replay re-derives the SAME id → UPSERT is
idempotent (no dup rows; a pre-restart ARMED row gets its terminal update after restart).

New table = trivial additive migration (CREATE TABLE IF NOT EXISTS + indexes; no data move),
runnable while the engine is up. Provided as a gated migration (bar_history pattern) — though far
simpler since there's no existing data. The observer can also guard with CREATE IF NOT EXISTS
defensively (archiver pattern).

## Q4 — Decision-path proof (observe-only)

The trade decision path is untouched. Concretely:
1. **Signal generation unchanged.** `SfpDetector.on_closed_bar` still returns the same
   `list[SfpEntrySignal]`, computed by the same `_maybe_fire` / `_advance_watch` branches. The
   transition buffer is **write-only**: nothing in `_maybe_fire`, `_advance_watch`, or the
   `on_closed_bar` control flow READS `_transitions`. Watches that exist, signals that fire, and
   their values are decided exactly as before; transitions are appended alongside.
2. **Observer decision path unchanged.** `_handle_signal` → `compute_geometry` → RiskAgent gate →
   `_place` → `_place_tp_leg` are byte-identical. The emit is a NEW observe-only step
   (`drain_transitions()` → persist) called AFTER the signal loop in `_process_symbol`, wrapped in
   try/except so it can never raise into the loop or affect a trade.
3. **No read-back.** Nothing reads `sfp_watch_state` or the heartbeat into any "whether/when/what
   to trade" branch. The dashboard reads them (separate, later step); the engine never does.

Proof artifacts at build time: (a) a diff showing the detector change is **purely additive**
(new `watch_id` field + `_transitions` appends at existing branch points + `drain_transitions()` —
zero edits to the return logic of `_maybe_fire`/`_advance_watch`/`on_closed_bar`); (b) the
observer diff showing `_handle_signal`/`_place`/`_place_tp_leg`/risk-gate hunks byte-unchanged;
(c) **parity test green** (`test_parity_streaming_matches_oracle` + `test_parity_includes_both_modes_across_seeds`); (d) full suite == baseline.

## Q5 — Loop heartbeat (trivial, independent)

The 15m loop: `run_loop` (`bitunix_sfp_observer.py:175`) → `_sleep_to_next_boundary` (L186) →
`process_once` (L191). Add ONE write at the end of each `process_once` cycle:

```python
db.set_agent_state(DIVISION, "loop_last_evaluated", {"ts": <utc-iso>}, db_url=self.db_url)
```

`agent_state` already exists (the observer uses it for peak-equity) — **no migration**, no key
collision (verified: zero `agent_state` rows for `bitunix_sfp` today). Replaces the cockpit's
current proxy (latest bar `inserted_at`) with a true loop-tick age. Wrapped so it never raises.

---

## Build-plan flags (for your review)
1. **Two files change** — `bitunix_sfp.py` (detector, prod `ad8e36f5`) + `bitunix_sfp_observer.py`
   (observer, prod `db831daf`). Both get md5 Gate-A/B. `bitunix.py` untouched.
2. **Parity is the load-bearing gate** — the detector is parity-pinned to the p6 oracle
   (`6e411762`). The change must keep `on_closed_bar`'s returned signals identical; parity test
   proves it. Existing detector tests assert on returned signals only (none construct `_Watch`).
3. **Migration** — new `sfp_watch_state` table via a gated additive migration (CREATE IF NOT
   EXISTS); can run with the engine up. Plus `agent_state` for heartbeat (no migration).
4. **Restart required** — observer/detector code change needs a restart to load (the watch emit
   does NOT hot-reload like `auto_execute`). Flagged.
5. **Warm-start behavior (decision needed):** on restart the detector replays history and would
   re-emit ARMED/terminal for historical watches. Options: (a) drain-and-DISCARD during
   `warm_start`, persist only LIVE transitions (simplest; near-miss has a small gap for any
   downtime window); (b) persist warm-start transitions too but only those within the last
   `watch_bars`/24h (idempotent UPSERT fills the downtime gap, no ancient flood). **Recommend (b).**
6. **`bos_watch_level` semantics:** dynamic per bar. v1 captures it at ARM (current candidate) and
   finalizes at CONFIRM. If the cockpit wants a per-bar-accurate live BOS target during the watch,
   add a cheap per-bar UPDATE of `bos_watch_level` (optional; flag for review).
7. **Dashboard wiring is a SEPARATE step** — this build produces the emit + schema only; pointing
   the Tier-B fragments at `sfp_watch_state` (instead of `_mock_*`) is its own change.

## What I will NOT do without your go
Build/modify the live detector or observer, run the migration, or deploy. Awaiting review.
