# R1+R2 (+R4) implementation plan — kalshi_copy_trader feed-health breaker

**Status: STAGED, NOT DEPLOYED. No code written, no DB write, no config change this session.**
Fixes the MaggieTheEagle `mass_disappearance` permanent-latch (see `_km_ROOT_CAUSE_REPORT.md`).
Base branch: **`claude-2026-07-29`** (== prod-live `e82a07d`). Drift-gate PASS (evidence below).

---

## 0. Design summary (what changes, conceptually)

Today the breaker is all-or-nothing: on a >=60% single-cycle disappearance it `continue`s — suppressing
the WHOLE whale and RETAINING the stale snapshot — with no way to tell a transient feed glitch from a
permanent legitimate removal (settlement). A settlement therefore latches it forever and spams one alarm
per cycle.

- **R1 (settlement-aware):** when the breaker trips, classify each removed ticker via Kalshi resolution.
  Tickers that **resolved/void** = legitimate exits -> process normally + advance the snapshot, NO alarm.
- **R2 (confirm-and-advance):** tickers still **active/unknown** (settlement inconclusive) = suspicious ->
  retain ONLY those, alarm **once**, and if the same suspicious set persists **N=3** cycles, accept it as
  real (advance + drop), reset. This de-spams and gives a recovery path even if the Kalshi check is down.
- **R4 (one-time latch clear):** OPTIONAL — R1 self-heals the current Maggie latch on the first
  post-deploy scan (mechanism proven below), so the manual snapshot write is only needed if we want the
  alarms to stop at the deploy instant rather than <=10 min later. Scripted + reviewable regardless.

Net effect on THIS incident: first post-deploy scan classifies both `KXFEDDECISION-26JUL-*` as
`resolved` -> `active_missing` empty -> normal processing -> exits are no-ops (unheld) -> snapshot advances
to `{KXFEDDECISION-26SEP-H0}` -> latch cleared, no alarm. Self-heals.

---

## 1. Exact code loci (worktree == prod, e82a07d)

File: `trading_corp/agents/strategies/kalshi_copy_trader.py` (1052 lines, LF-md5 `720df3d8c5cadef044176566a09db3b9`)

| Locus | Line | Role |
|---|---|---|
| `run_scan_cycle` (per-whale loop) | 248 / 302-410 | where the breaker + snapshot-advance live |
| diff sets (`removed_tickers`, `carryover_tickers`, `new_tickers`) | 325-329 | inputs to the breaker |
| **breaker block to rework** | **338-343** | `if self._is_mass_disappearance(...): _queue_feed_anomaly(...); continue` |
| new-entry loop | 348-381 | unchanged |
| carryover loop (reads `current_by_ticker[ticker]`) | 384-393 | unchanged; NOTE it KeyErrors on a ticker not in the feed |
| exit loop (`_emit_exit`) | 397-405 | unchanged; `_emit_exit` returns None when unheld (523) |
| **snapshot-advance write** | **407** | `self._save_whale_snapshot_raw(whale, new_snapshot)` |
| `_emit_exit` (no-op when unheld) | 505 / 521-526 | `if not our_outcome or copy_usd<=0.0: return None` |
| `_feed_cfg` (reads `feed_health`) | 887-888 | R1/R2 tunables land here |
| `_is_mass_disappearance` (60% / min 2) | 890-903 | UNCHANGED (still the trip gate) |
| `_queue_feed_anomaly` | 905-924 | reused; gated to fire once (R2) |
| `_record_fetch_failure` / `_consecutive_fetch_failures` | 926-948 | separate breaker, untouched |
| `drain_feed_alarms` | 950-954 | unchanged |
| state helpers `load_agent_state`/`set_agent_state` imported | 77 | R2 counter uses these |
| `_save_whale_snapshot_raw` / `_load_whale_snapshot` | 797 / 784 | snapshot read/write |

File: `trading_corp/brokers/kalshi.py` (495 lines, LF-md5 `18626cf0ddcdf6c3663be7d9602abbba`)
- **`get_market_resolution(ticker)`** — line 309-367. **R1 reuses this AS-IS** (no broker change needed).

File: `config/strategies.yaml` (LF-md5 `6af510f67425a82f4208677a5c4558ef`)
- `kalshi_copy_trader:` block at 1643; **no `feed_health:` sub-block exists** -> R1/R2 add one (hot-reload).

---

## 2. R1 settlement-check call (reuse existing; $0)

**Primitive (already in code, already used by `_emit_exit` for exit pricing):**
```python
res = await trade_tape_fetcher.get_market_resolution(ticker)   # brokers/kalshi.py:309
# -> {"status": "resolved"|"void"|"pending"|"not_found", "result": "yes"|"no"|"void"|None, ...}
```
Under the hood it calls pykalshi `self._client.get_market(ticker)` and reads the market's `.result` field:
- `.result in {yes,no}`  -> `status="resolved"`  (THE settlement case; Fed markets return this)
- `.result == "void"`    -> `status="void"`      (cancelled/refunded; also legitimate)
- empty `.result`        -> `status="pending"`   (still active OR closed-but-not-yet-determined)
- API error / stub       -> `status="not_found"`

**Classification rule (R1):**
- `status in {"resolved","void"}` -> **settled** (legitimate removal; do NOT alarm; advance).
- anything else (`pending`,`not_found`,exception,fetcher is None) -> **active_missing** (suspicious -> R2).
  Conservative: we only auto-clear on a CONFIRMED settlement, never on ambiguity.

**Cost / auth:** GET market is a read; **$0** on Kalshi. Uses existing Kalshi creds already loaded on prod
(same client `_emit_exit` uses). NOT Apify — Apify budget untouched. Only called on anomaly cycles, once
per removed ticker (here: 2 calls/cycle until cleared, then 0).

**Confirmed live (this session, Kalshi public API):** `KXFEDDECISION-26JUL-H0`=finalized/result=yes,
`-26JUL-H25`=finalized/result=no, `-26SEP-H0`=active/result="" -> get_market_resolution would return
`resolved`, `resolved`, `pending` respectively. Exactly the split R1 needs.

---

## 3. R2 cycle-counter location: `agent_state` (persistent, signature-keyed)

**Recommendation: `agent_state`, NOT in-memory.** Rationale: the latch is a *persistent-state* problem;
an in-memory counter (like `_consecutive_fetch_failures`) resets on every process restart, so a restart
mid-episode would restart the N-cycle countdown and could delay recovery. The write cost is trivial
(anomaly cycles are rare; 10-min cadence). Uses the already-imported `load_agent_state`/`set_agent_state`.

- **Key:** `feed_anomaly_streak:{whale}` (agent = `kalshi_copy_trader`).
- **Value:** `{"sig": [sorted active_missing tickers], "count": int, "first_ts": iso}`.
- **Bump:** if stored `sig` == current sorted `active_missing` -> `count += 1`; else reset `count = 1`,
  new `sig`. Returns `count`.
- **Clear:** delete/empty the key. Called when: (a) R1 fully clears (no active_missing), (b) R2 confirms
  at count>=N, (c) a normal healthy cycle for the whale (no mass_disappearance) — so a resolved episode
  doesn't leave a stale streak.
- **N (confirm cycles):** `feed_health.anomaly_confirm_cycles`, default **3** (~30 min at 10-min cadence).

*(Alternative if you prefer zero DB writes: in-memory dict on the agent instance — simpler, but loses the
count on restart. Not recommended given the latch is exactly a restart-surviving condition.)*

---

## 4. Pseudo-diff (kalshi_copy_trader.py)

Replace the breaker block (338-343) and add a retained-suspicious re-insert before the save (407).
New helper methods added near `_is_mass_disappearance`.

```python
# --- replace 338-343 --------------------------------------------------------
retained_suspicious: set[str] = set()
if self._is_mass_disappearance(prev_tickers, removed_tickers):
    settled_missing, active_missing = await self._classify_removed(
        removed_tickers, trade_tape_fetcher, logger_agent,
    )
    confirm_n = int(self._feed_cfg().get("anomaly_confirm_cycles", 3))
    if not active_missing:
        # R1: every vanished ticker resolved/void -> legitimate settlement.
        # Fall through to normal processing; exits are no-ops unless we held a copy.
        self._clear_anomaly_streak(whale)
    else:
        streak = self._bump_anomaly_streak(whale, active_missing)
        if streak < confirm_n:
            # Still suspicious. Alarm ONCE (first cycle of this signature) and
            # RETAIN only the still-active vanished tickers; advance everything else
            # (settled removals processed as exits, new entries, carryovers).
            if streak == 1:
                self._queue_feed_anomaly(
                    logger_agent, whale=whale, n_prev=len(prev_tickers),
                    n_removed=len(active_missing), n_curr=len(curr_tickers),
                    reason="mass_disappearance",
                )
            removed_tickers = removed_tickers - active_missing   # don't exit the retained ones
            retained_suspicious = active_missing
        else:
            # R2: persisted N cycles, settlement inconclusive -> accept as real.
            self._queue_feed_anomaly(
                logger_agent, whale=whale, n_prev=len(prev_tickers),
                n_removed=len(active_missing), n_curr=len(curr_tickers),
                reason="confirmed_real_after_n_cycles",
            )
            self._clear_anomaly_streak(whale)
            # fall through: active_missing now flow through the exit loop + drop.
else:
    self._clear_anomaly_streak(whale)   # healthy cycle -> reset any stale streak
# ... existing new/carryover/exit loops run unchanged ...

# --- insert just before line 407 (_save_whale_snapshot_raw) ------------------
for t in retained_suspicious:
    # keep the suspicious-but-unconfirmed tickers in the book so we don't lose
    # track while R2 waits out the confirm window (they are NOT in the feed, so
    # they never enter the new/carryover loops).
    new_snapshot[t] = prev_snapshot.get(t) or {}
self._save_whale_snapshot_raw(whale, new_snapshot)
```

New helpers (near 890):
```python
async def _classify_removed(self, removed, fetcher, logger_agent):
    """Split removed tickers into (settled, active_missing) via Kalshi resolution.
    resolved/void -> settled; pending/not_found/error/no-fetcher -> active_missing."""
    settled, active = set(), set()
    check_on = bool(self._feed_cfg().get("settlement_check_enabled", True))
    can_check = check_on and fetcher is not None and hasattr(fetcher, "get_market_resolution")
    for t in removed:
        if not can_check:
            active.add(t); continue
        try:
            status = ((await fetcher.get_market_resolution(t)) or {}).get("status")
        except Exception as e:
            log.warning("kalshi_copy_trader: resolution check failed for %s: %s", t, e)
            status = None
        (settled if status in ("resolved", "void") else active).add(t)
    return settled, active

def _bump_anomaly_streak(self, whale, active_missing) -> int:
    sig = sorted(active_missing)
    rec = self._load_anomaly_streak(whale)
    count = (int(rec.get("count", 0)) + 1) if rec.get("sig") == sig else 1
    set_agent_state(self.name, f"feed_anomaly_streak:{whale}",
                    {"sig": sig, "count": count}, db_url=self._db_url)
    return count

def _load_anomaly_streak(self, whale) -> dict:
    if not self._db_url: return {}
    rec = load_agent_state(self.name, f"feed_anomaly_streak:{whale}", db_url=self._db_url)
    return rec[0] if rec and isinstance(rec[0], dict) else {}

def _clear_anomaly_streak(self, whale) -> None:
    if not self._db_url: return
    set_agent_state(self.name, f"feed_anomaly_streak:{whale}", {}, db_url=self._db_url)
```

**Blast radius:** 1 code file (kalshi_copy_trader.py) + 1 config (strategies.yaml). `brokers/kalshi.py`
UNCHANGED (reuse). `main.py` UNCHANGED (the loop already drains + pushes whatever we queue). Optional
cosmetic: main.py:4042 alarm text ("Check Apify feed health") could be made reason-aware, but it already
renders `alarm.get('reason')`, so `confirmed_real_after_n_cycles` shows through — defer.

**Noted follow-up (not blocking):** if we ever HOLD a copy of a market that settles, `_emit_exit` already
prices it via `get_market_resolution` (resolved -> $1/$0). So R1's "process settled as exits" path is
already correctly priced for held copies. No change needed.

---

## 5. Config addition (strategies.yaml, under `kalshi_copy_trader:`)

```yaml
  feed_health:
    mass_exit_threshold_pct: 60        # unchanged trip threshold (explicit now)
    min_positions_for_check: 2         # unchanged
    settlement_check_enabled: true     # R1: classify removed tickers via Kalshi resolution
    anomaly_confirm_cycles: 3          # R2: accept as real after N persistent cycles
```
Hot-reloads via `_reload()` (no restart needed for the CONFIG), but the R1/R2 CODE needs a process
restart to take effect. `_is_mass_disappearance` keeps reading the same threshold keys — behavior of the
existing gate is unchanged.

---

## 6. R4 — one-time snapshot clear (OPTIONAL; scripted + reviewable)

**Only needed if you want the current Maggie alarms to stop at the deploy instant instead of <=10 min
later. R1 self-heals it on the first post-deploy scan regardless.** Include but recommend skipping unless
instant-clear is wanted.

Script `r4_clear_maggie_snapshot.py` (run on prod venv, AFTER an explicit go; makes a backup first):
```python
# READ current snapshot, back it up, rewrite to keep ONLY the still-active ticker(s).
import json, datetime
from trading_corp.persistence.db import load_agent_state, set_agent_state
DB = "sqlite:////home/azureuser/trading_corp/data/trading_corp.db"
AGENT, KEY = "kalshi_copy_trader", "positions:MaggieTheEagle"
rec = load_agent_state(AGENT, KEY, db_url=DB)
snap = rec[0] if rec else {}
print("BACKUP:", json.dumps(snap))                       # capture before mutate
KEEP = {"KXFEDDECISION-26SEP-H0"}                         # the one active market (verified active)
new = {k: v for k, v in snap.items() if k in KEEP}
assert set(new) == KEEP, f"expected {KEEP}, got {set(new)}"   # guard
# set_agent_state(AGENT, KEY, new, db_url=DB)            # <-- COMMENTED; uncomment only on go
print("WOULD WRITE:", json.dumps(new))
```
Reviewable dry-run prints BACKUP + WOULD-WRITE; the actual `set_agent_state` line stays commented until
explicit approval. Rollback = re-write the printed BACKUP. This is a DB write -> **operator go required.**

---

## 7. Deploy runbook (patch-based; prod is NOT git)

Base = `claude-2026-07-29` (== e82a07d). Target files proven identical to prod (drift-gate below), so the
kalshi drift hazard does NOT apply to these files.

1. **Build** on branch `claude-2026-07-29`: edit `kalshi_copy_trader.py` + `strategies.yaml`; add/extend
   `tests/test_kalshi_feed_health_guard.py`. Run the suite locally
   (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_kalshi_feed_health_guard.py tests/test_kalshi_copy_trader.py`).
2. **Gate-A drift-gate (pre-apply):** re-pull prod LF-md5 of the 2 files and assert == the baseline below.
   If prod moved, STOP + reconcile.
   - `kalshi_copy_trader.py` LF-md5 baseline: `720df3d8c5cadef044176566a09db3b9`
   - `strategies.yaml`        LF-md5 baseline: `6af510f67425a82f4208677a5c4558ef`
   - `brokers/kalshi.py`      LF-md5 (unchanged, sanity): `18626cf0ddcdf6c3663be7d9602abbba`
3. **Backup + apply** via ssh (azureuser owns `/home/azureuser/trading_corp/...`; NO sudo): copy prod
   files to `.bak_feedhealth_r1r2_20260730`, LF-normalize the new files, write them, re-md5 to confirm the
   intended new hash landed.
4. **Restart** trading-corp.service via **Azure RunCommand (root, no-sudo)** — the established pattern
   (systemctl restart needs root; do NOT sudo). Confirm NRestarts increments and a NEW MainPID.
5. **Verify** (section 8). If bad -> restore `.bak_feedhealth_r1r2_20260730`, restart, done.

*Executable split:* steps 1-3 + 5 are ssh/local (I can stage/run on go). Step 4 (root restart) is the
operator's Azure RunCommand. auto_execute/roster/threshold: UNCHANGED.

---

## 8. Verify step (post-deploy)

**A. Current latch clears (R1 self-heal):**
- Within one scan cycle (<=10 min) OR immediately if R4 ran: `agent_state positions:MaggieTheEagle`
  advances to a single key `KXFEDDECISION-26SEP-H0`, `updated_ts` fresh.
- No NEW `kalshi_copy_feed_anomaly` audit rows after the restart; Telegram stops.
- `feed_anomaly_streak:MaggieTheEagle` is empty/cleared.

**B. Unit tests (extend `tests/test_kalshi_feed_health_guard.py`) — the core behavioral proof:**
1. **settled removal -> NO alarm, snapshot advances:** whale prev={A,B,C}; feed returns {C}; stub
   `get_market_resolution` returns `resolved` for A,B. Assert: 0 queued alarms, snapshot == {C},
   streak cleared, A/B processed as (no-op) exits.
2. **active removal -> alarm ONCE + retain, then confirm after N:** feed returns {C}; stub returns
   `pending` for A,B. Cycle 1: exactly 1 alarm, snapshot still {A,B,C} (A,B retained), streak=1.
   Cycles 2..N-1: 0 new alarms, still retained. Cycle N (=3): 1 "confirmed_real_after_n_cycles" alarm,
   snapshot advances to {C}, streak cleared.
3. **mixed removal:** A resolved, B pending. Cycle 1: 1 alarm, snapshot={C,B} (B retained, A dropped as
   settled). Confirms per-ticker classification.
4. **fetcher None / resolution raises:** treated as active_missing (R2 path), never auto-drops on
   ambiguity (conservative-safety regression guard).
5. **held-copy settled:** prev_pos has our_side/size -> `_emit_exit` emits a priced exit ($1/$0), and the
   ticker still advances out of the snapshot (guards that R1 doesn't silently swallow a real exit).

**C. Non-regression:**
- `_is_mass_disappearance` threshold behavior unchanged (existing tests still pass).
- `_record_fetch_failure` path (whole-feed outage) untouched.
- AI.EDGE / other whales still process normally; no new alarms for them.

---

## 9. Drift-gate evidence captured this session (LF-normalized md5, prod == worktree e82a07d)
```
kalshi_copy_trader.py   720df3d8c5cadef044176566a09db3b9   (1052 lines)  MATCH
kalshi_apify_client.py  2f97a333dcfb7c2f39876869e5e50ef2   (368 lines)   MATCH (untouched by fix)
brokers/kalshi.py       18626cf0ddcdf6c3663be7d9602abbba   (495 lines)   MATCH (reused, untouched)
strategies.yaml         6af510f67425a82f4208677a5c4558ef                 (feed_health block ABSENT)
```
Live engine: trading-corp.service MainPID **450695** (py 450709), up since 2026-07-29 02:57:34Z,
NRestarts=0 (the PMCC e82a07d deploy — orthogonal to kalshi; feed files identical pre/post).

---

## 10. Open decisions for operator (before build)
1. **N (anomaly_confirm_cycles) = 3?** (~30 min recovery if the Kalshi check is unreachable.) OK or tune.
2. **R2 counter in agent_state (recommended) vs in-memory?** (persistent vs restart-reset.)
3. **Run R4 at deploy (instant clear) or let R1 self-heal (<=10 min)?** Recommend: skip R4, let R1 heal.
4. **Alarm-once semantics:** warn on first suspicious cycle + one "confirmed" note at N. OK?
5. **main.py alarm text tweak** (reason-aware wording) — include or defer (cosmetic)?

---

## 11. BUILD STATUS — COMPLETE & STAGED (2026-07-30)

Built on branch `claude-2026-07-29` per approved decisions (N=3, agent_state counter, skip R4,
two-alarm w/ zero-alarm on confirmed settlement, reason-aware main.py). **Not deployed. No DB write.**

Files changed (`git diff --stat`):
- `trading_corp/agents/strategies/kalshi_copy_trader.py`  +153/-17  (R1 `_classify_removed`, R2 streak
  helpers, breaker rework, retained-suspicious re-insert, reason-aware internal warning)
- `trading_corp/main.py`  +34/-4  (reason-aware Telegram text: settlement=silent, suspicious=feed-gap
  wording, confirmed=auto-resolved note, fetch-fail=FEED DOWN)
- `config/strategies.yaml`  +16  (new `feed_health:` block: threshold 60, min 2,
  settlement_check_enabled true, anomaly_confirm_cycles 3)
- `tests/test_kalshi_feed_health_guard.py`  +155  (6 new tests + `_StubResolver`)
- `r4_clear_maggie_snapshot.py`  NEW  (reviewable DRY-RUN artifact; write line commented; NOT run)

Validation (local Python 3.14.4 — deps present):
- `py_compile` all 4 modified `.py` files: **OK**.
- `pytest tests/test_kalshi_feed_health_guard.py`: **11 passed** (5 pre-existing + 6 new).
- `pytest` feed-health + `test_kalshi_copy_trader.py` + `test_kalshi_copy_trader_sports_skip.py`:
  **46 passed**, no regressions. (Existing `test_empty_feed_suppresses_exits_and_retains_snapshot`
  still green — empty-feed/None-fetcher path still retains + alarms once.)

New-test coverage map (matches the requested cases):
- (a) `test_settled_disappearance_advances_snapshot_no_alarm` — settlement -> advance, ZERO alarms.
- (b) `test_active_disappearance_alarms_as_suspicious` — active removal still alarms (safety preserved).
- (c) `test_suspicious_persists_confirms_after_n_cycles` — persists N=3 -> 1 confirm alarm, advance;
  exactly 2 alarms total (no per-cycle spam).
- (d) `test_maggie_fed_settlement_self_heals` — the exact incident tickers self-heal to {-26SEP-H0}.
- (e) `test_held_copy_settled_still_exits` — R1 doesn't swallow a real held exit ($1.00 priced).
- (f) `test_disappearance_suspicious_when_resolution_unavailable` — no fetcher -> retain, safe direction.

Deploy base still clean (re-verify at Gate-A): `kalshi_copy_trader.py 720df3d8`, `strategies.yaml 6af510f6`,
`main.py 302c06e7`, `brokers/kalshi.py 18626cf0` all == prod (LF-md5). Restart required (code change);
config hot-reloads. **Awaiting operator go to deploy.**
