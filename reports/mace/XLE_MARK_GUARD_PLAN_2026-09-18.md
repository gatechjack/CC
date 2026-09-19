# MACE PT Mark-Trust Guard + XLE rung reset — weekend implementation plan (2026-09-18)

## Context

On 2026-09-18 a live XLE 2026-10-30 x2 iron condor (opened 9/17, ~$0.30 credit, 42 DTE) was pushed into
CLOSING by a **false profit-target trigger**. MACE's synthetic 50% PT fires when the fresh per-contract
cost-to-close MID (`executor.mark()`) drops to `<= 0.5 x credit` (= 0.15). RH returned a **stale/erroneous
mark of 0.13** (identical twice, 14 min apart) that falsely satisfied the PT on a position that was NOT near
profit (RH true value ~0.41). Proof it was bogus: the identical-strike **28-DTE sibling** (XLE 10/16
60/59/70/71) marked **0.205 LIVE** — a longer-dated same-strike condor cannot be worth LESS than a
shorter-dated one; XLE ATM IV was flat 9/17->9/18 (no vol crush); the whole 42-DTE cohort marked 98-106% of
credit. The false PT flipped the rung to CLOSING; the close then empty-response'd 35x (RH refused the closing
combo on the same bad-quote contracts) and never filled — so $0 realized impact (the empty-response
accidentally blocked the bad PT). Diagnosis: `reports/mace/XLE_STUCK_CLOSING_DIAG_2026-09-18.md`.

Intended outcome (design pre-decided by Jack — implement, do not redesign): **one code fix** — a MARK-TRUST
GUARD on the PT trigger that fires PT only on a mark that is TIMELY (not frozen/stale) and SANE (no
arbitrage/structural violation); an untrusted mark HOLDS the rung + ALERTS via the existing channel. **No**
volume gate. **No** change to the deployed deferral/hold/CLOSING logic. **Then** a one-time DB reset of the
stuck XLE rung CLOSING->open (AFTER the guard deploys, so a still-bad Monday quote is rejected by the guard
instead of re-firing the false PT).

Scope: MACE-only (`trading_corp/mace/*` + `config/mace.yaml` + `config.py` + tests). Box runs from files (no
.git). Single agent, one branch, box-only deploy (no FF-push; git-divergence reconcile deferred). DEPLOY /
RESTART / arm / halt / DB-write are Jack-reserved.

**Deliverable:** on approval, the FIRST action is to copy+commit this plan to
`reports/mace/XLE_MARK_GUARD_PLAN_2026-09-18.md` (the review trail Jack asked for); then execute in the
order of section 6. Nothing is built until then.

**Decisions confirmed by Jack (2026-09-18):** (1) branch off **2362db46** (deployed box-truth), NOT
c2593e34; (2) tuning constants live in **`config/mace.yaml`** (`management.mark_guard`) — accepted that
`config_hash` MOVES off `bfde856f` and the deploy restarts to re-read; boot-verify treats **config_hash
CHANGED as CORRECT**.

---

## 0. Branch base + pre-build verification (RESOLVE FIRST)

**Discrepancy:** the task says branch off `c2593e34`, but the box is running the **trigmid build**
(commit **2362db46** = c2593e34 + the 9/11 winner-cap + 9/18 trigger-mid-anchor fixes; deployed blobs
manager md5 `a83b62e1`, execution md5 `a65955bb`, confirmed by the 14:23Z diagnosis pull). c2593e34 is the
PRE-trigmid base — branching there would DROP the deployed winner-cap/defer fix (the very logic that made the
9/18 13:43 close DEFER correctly). **CONFIRMED: branch off `2362db46` (box-deployed truth).**

Pre-build gate (RO runner, sanctioned channel): re-read box `md5sum` of `mace/manager.py` (expect
`a83b62e1`), `mace/execution.py` (`a65955bb`), `mace/strategy.py`, `mace/config.py`, `mace/notify.py`, and
`sha256(config/mace.yaml)` (`bfde856f...`) to confirm what I branch from == what is running (the 18:17Z
restart ran the same files; verify). If any differs from the expected trigmid blobs -> STOP + reconcile
before building.

---

## 1. Read-only investigation findings (current code)

- **PT decision path (PURE):** `strategy.evaluate_management(rung, mark, spot, now_et, cfg, symbol_cfg, *,
  exdiv_within) -> ManageDecision(rung_id, exit_reason, detail)` (`trading_corp/mace/strategy.py:817-855`).
  Precedence stop > **PT** > time > exdiv. PT branch (lines 840-845): `pt_target = m.pt_pct_of_credit *
  credit_actual; if mark <= pt_target: return ManageDecision(rung_id, EXIT_PT, ...)`. No I/O, no clock, no DB.
- **Intercept point:** `manager._manage_one` (`manager.py:407-456`). Sequence: `mark = await
  executor.mark(spec)` (416) -> `store.set_live_state(...)` writes `mace_rung_live` (425-428, fail-safe) ->
  `decision = evaluate_management(...)` (440) -> `if not decision.should_exit: return None` (442) ->
  `close_rung(rung, reason, pricing, defer, trigger_mid=mark)` (454). **The guard slots in at line ~442**:
  when `decision.exit_reason == EXIT_PT`, run the trust check; untrusted -> `return None` (hold) + alert.
  `self.notifier` and `self._audit` are both in scope here. A CLOSING rung never reaches this (early return
  at 413) — the guard only affects the OPEN->close transition, exactly the bug surface.
- **Prior-mark availability:** `mace_rung_live(rung_id, mark, spot, ts)` persists the last tick's mark
  (survives restart). BUT `set_live_state` (425) overwrites it with THIS tick's mark BEFORE the decision —
  so the guard must capture the prior value at the TOP of `_manage_one` before the overwrite.
- **Sibling data:** rungs are iterable via `store.load_all()` / the loaded `_MANAGED_STATUSES` set;
  `CondorSpec` exposes `short_put/long_put/short_call/long_call` + `strikes_label()` + `expiry`
  (`domain.py:150-199`). Existing per-symbol aggregation pattern in `strategy.py`
  (`open_rung_count`, `net_option_positions`) shows the iterate-and-group idiom to reuse. No existing
  "same-strike sibling" finder — add a small pure helper.
- **Alert plumbing to REUSE (no new system):** `MaceNotifier` (`notify.py:92-133`) pushes formatted strings
  through a fire-and-forget Telegram bridge (`_MaceChannelBridge` in main.py, `asyncio.create_task` — safe
  from the 5-min loop, non-blocking). `notifier.reject(symbol, detail)` -> `format_reject` ->
  `"WARN MACE {symbol} order rejected - {detail}"` is the right WARN-level reuse (the stuck-rung URGENT alert
  is `close_exhausted`/`breaker`; a held PT is a softer anomaly, so `reject` fits). Activity Pulse
  (`web/mace_view.py`) renders `audit_event` rows with `actor IN (robinhood_mace,...) AND kind LIKE 'mace_%'`
  and assigns **"alert" visual priority** to any kind containing `reject|error|exception|partial|
  unconfirmed|unpriceable|standdown|halt`. So an audit kind **`mace_pt_mark_reject`** shows in the Pulse as
  an alert. `self._audit(kind, **payload)` -> `logger_agent.log_event("robinhood_mace", kind, payload)` ->
  `audit_event(ts, actor, kind, payload_json)`.
- **Deferral (unchanged):** the guard only needs to NOT-fire PT (`return None`); the rung stays OPEN and is
  re-evaluated next tick. No `close_rung`/deferral change. (When PT legitimately fires later on a trusted
  mark, the deployed winner-cap/defer at `execution.py:close_rung` handles fillability — no volume gate.)
- **Test harness:** `tests/test_mace_execution.py` (FakePort with settable `leg_quote` bid/ask,
  `RecChannel` spy asserting `chan.any("substr")`, `_executor`/`_open_rung` helpers, in-memory `db.SCHEMA`
  via `_conn()`); `tests/test_mace_strategy_manage.py` (pure `evaluate_management` precedence tests);
  `tests/test_mace_loops.py` (manager/`manage_tick` level); `tests/conftest.py`. Notifier spied via a fake
  channel that records `.push()` calls.

---

## 2. The guard — precise design

Add a **pure** trust assessor in `strategy.py` and wire it (with the impure inputs: sibling fresh-mark,
prior mark, in-memory state) from `manager._manage_one`. Gates **ONLY `EXIT_PT`** (stop/time/exdiv are
risk-reducing and must still fire — deliberately unguarded; see scope note).

`assess_pt_mark_trust(...) -> (trusted: bool, reason: str)` returns TRUSTED = **TIMELY AND SANE**.

**TIMELY (not frozen):** reject if the fresh mark is EXACTLY equal (rounded to cents) to this rung's mark on
the previous PT-eligible tick for **>= 2 consecutive** such ticks. Tracked by a small in-memory per-rung
counter on the manager (reset when the mark changes or on restart). Rationale: the incident mark was
bit-identical (0.13) across cycles; a live quote virtually never stays bit-identical two ticks running.
Exact-equality + >=2 minimizes false-reject of a genuinely stable quote; a false-hold is low-harm (PT is
non-urgent, re-evaluates next tick).

**SANE (no arbitrage / structural violation):**
- **Structural:** reject if NOT (`entry_tick_usd <= mark < width_dollars`). A cost-to-close below one tick
  or >= the width is arbitrage-impossible for a defined-risk short condor being bought back.
- **Sibling (PRIMARY):** among OPEN/CLOSING rungs, find same symbol + **identical 4 strikes** + **shorter
  DTE**. For each, fetch a FRESH mark via `executor.mark(sibling.spec)` (accurate, same-instant; the
  sibling's contracts differ from the bad-quote contracts, so its mark is clean — confirmed: the 10/16
  sibling marked fine while 10/30 was bad). Let `max_sib = max(fresh sibling marks)`. Reject if
  `mark < max_sib - epsilon` (epsilon = 1 tick). A longer-dated same-strike condor cannot cost LESS to
  close. (PT's failure mode is a too-LOW mark, so only the shorter-dated LOWER bound matters.)
  Incident: candidate 0.13 < sibling 0.205 - 0.01 -> REJECT.
- **Fallback (no same-strike shorter-dated sibling):** decay-plausibility bound. Reject if
  `DTE > time_exit_dte (21)` AND `mark < last_trusted_mark * (1 - MAX_CYCLE_DROP)`, with
  `MAX_CYCLE_DROP = 0.35`. A >35% single-cycle collapse in cost-to-close while far from the time-exit window
  (theta is slow at high DTE, and no comparable spot move) is implausibly fast. `last_trusted_mark` = the
  rung's last mark that passed the guard (in-memory; seeded from persisted `mace_rung_live.mark` on the first
  post-restart touch — best-effort). If NO baseline AND no sibling exist (fresh restart, first-ever mark):
  **fail-closed** — treat untrusted for this one tick, record the mark as the provisional baseline, HOLD
  WITHOUT alerting (benign one-cycle confirmation delay); PT fires next tick once a baseline exists.

**On UNTRUSTED (timely OR sane fails, excluding the silent fail-closed-no-baseline case):**
- `return None` from `_manage_one` -> PT does not fire, rung stays OPEN (existing hold behavior).
- `self._audit("mace_pt_mark_reject", rung_id=, symbol=, mark=, reason=<stale|arbitrage|structural>,
  sibling_rung_id=, sibling_mark=, pt_target=, detail=...)` -> Activity Pulse (alert priority).
- `self.notifier.reject(symbol=rung.symbol, detail=f"PT held - untrusted mark {mark:.2f} ({reason}); ...")`
  -> Telegram (fire-and-forget).

**State:** one in-memory dict on `MaceManager.__init__` — `self._pt_mark_trust: dict[str, _MarkTrustState]`
(`last_mark`, `unchanged_count`, `last_trusted_mark`). Code-only, no schema change. Restart-benign: the
SANE sibling + structural checks are stateless and guard immediately (on Monday the 10/16 sibling still
exists, so a persistent bad 0.13 is caught at once); only the frozen-counter + fallback baseline rebuild over
1-2 ticks.

**Constants live in `config/mace.yaml` (Jack's decision) — a new `management.mark_guard` block:**
```yaml
management:
  # ... existing keys unchanged ...
  mark_guard:
    enabled: true            # kill-switch; false -> guard is a pass-through (PT fires as today)
    frozen_cycles: 2         # TIMELY: reject a mark bit-identical (to cents) across >= this many consecutive PT-eligible ticks
    sane_epsilon_usd: 0.01   # SANE: tolerance (1 tick) on the sibling arbitrage compare + structural floor
    max_cycle_drop_pct: 0.35 # FALLBACK: reject a single-cycle cost-to-close collapse > this while DTE > time_exit_dte
```
`config.py` `load_mace_config` must PARSE + fail-fast VALIDATE this block into the frozen config (a
`MarkGuardConfig` dataclass on `ManagementConfig`, e.g. `cfg.management.mark_guard`): `enabled` bool;
`frozen_cycles` int >= 1; `sane_epsilon_usd` float > 0; `max_cycle_drop_pct` float in (0,1). Follow the
existing optional-with-default pattern (mirrors `exit_winner_band` / `min_strike_separation_usd`) so the
block is validated but a defensive default exists. The pure `assess_pt_mark_trust` takes these as params
(stays pure/testable). **Timeliness note:** RH option-chain rows carry no reliable per-quote timestamp, so
"timely" is implemented as the frozen-cycles (bit-identical across N ticks) test, NOT a wall-clock quote-age
bound.

**config_hash MOVES off `bfde856f`.** The exact new sha256 cannot be predicted in this plan (it depends on
the final config bytes); the BUILD step finalizes `config/mace.yaml`, runs `sha256sum config/mace.yaml`, and
RECORDS that value as the expected new hash in the deploy manifest. Boot-verify criterion FLIPS to:
`config_hash != bfde856f AND == sha256[:12] of the deployed config/mace.yaml` (a config edit MUST move the
hash; an unchanged hash = the edit did not take = FAIL). The deploy ledger + memory anchors that reference
`bfde856f` update to the new hash in the wrap.

---

## 3. File / scope list (MACE-scoped; NO shared-trio file)

- `trading_corp/mace/strategy.py` — add pure `assess_pt_mark_trust(...)`, a `_MarkTrust` result, and a pure
  `shorter_dated_same_strike_siblings(rungs, rung)` helper. Thresholds arrive as params from
  `cfg.management.mark_guard` (function stays pure/testable). `evaluate_management` UNCHANGED (precedence
  tests stay green).
- `trading_corp/mace/manager.py` — in `_manage_one`: capture prior `mace_rung_live` mark before
  `set_live_state`; on `decision.exit_reason == EXIT_PT`, gather sibling fresh-mark + prior/trust-state, call
  the assessor, suppress+alert if untrusted, update the trust-state. Add `self._pt_mark_trust = {}` in
  `__init__`.
- `trading_corp/mace/execution.py` — add `RungStore.get_live_state(rung_id) -> Optional[tuple[float|None,
  str]]` (small additive READ, mirrors `set_live_state`).
- `config/mace.yaml` — add the `management.mark_guard` block (section 2). **`config_hash` MOVES** off
  `bfde856f` -> deploy MUST restart to re-read (config is loaded once at boot).
- `trading_corp/mace/config.py` — parse + fail-fast validate the `mark_guard` block into a `MarkGuardConfig`
  frozen dataclass on `ManagementConfig` (mirror the `exit_winner_band` optional-with-default pattern in
  `load_mace_config`). A malformed block aborts the load (fail-fast) -> a clean boot proves it parsed.
- Tests: `tests/test_mace_strategy_manage.py` (pure assessor unit tests) + a manager-level test in
  `tests/test_mace_loops.py` or new `tests/test_mace_mark_guard.py` (FakePort + RecChannel: PT suppressed +
  alert + rung stays open).
- **No shared-trio (data_exec/ceo_graph/risk) file touched.** If review finds one needed -> STOP + flag.

---

## 4. Test plan

Pure `assess_pt_mark_trust` (fast, `test_mace_strategy_manage.py`):
1. **9/18 replay:** candidate mark 0.13, credit 0.30, one shorter-dated same-strike sibling fresh mark 0.205
   -> `trusted=False, reason="arbitrage"`.
2. **Fresh sane:** candidate 0.14 (<= pt_target 0.15), sibling 0.12 (shorter-dated legitimately cheaper) ->
   `trusted=True` -> PT fires.
3. **No-sibling fallback OK:** no same-strike shorter sibling, DTE 42, prior trusted 0.28, candidate 0.20
   (29% drop < 35%) -> `trusted=True`.
4. **No-sibling fallback REJECT:** same but candidate 0.13 (54% drop > 35%, DTE>21) -> `trusted=False,
   reason="arbitrage"` (decay-implausible).
5. **Timely-but-insane:** changed mark but < sibling -> reject (sane). **Stale-but-sane:** mark == prior for
   2 cycles even though >= sibling -> reject (timely).
6. **Structural:** mark >= width or < 1 tick -> reject.
7. **Fail-closed no-baseline:** first-ever mark, no sibling -> untrusted THIS tick, no alert; second tick with
   baseline -> fires if sane.

Manager-level (FakePort + RecChannel, `test_mace_mark_guard.py`):
8. Seed the candidate rung + a shorter-dated same-strike sibling in-memory DB; FakePort quotes give candidate
   mid 0.13 and sibling mid 0.205; run `manager.manage_tick`. Assert: NO `close_rung` (rung stays `open`),
   `chan.any("PT held")` (Telegram), and an `audit_event` row kind `mace_pt_mark_reject`.
9. Trusted-mark control: candidate 0.14 >= sibling -> `close_rung` IS called (PT fires, pricing="winner").

Regression: **full box-scratch suite green, 0 new failures.** The box-scratch runner MUST carry
`-p no:pytest_ethereum` (the box venv autoloads a broken `web3.tools.pytest_ethereum` plugin that crashes
COLLECTION — hard-won rule; do NOT trust a clean local pyproject to mean the box is clean). `evaluate_management`
precedence tests unchanged (guard lives in manager, not the pure precedence fn).

---

## 5. DB reset of the stuck XLE rung (RESERVED write; AFTER the guard is live)

- **Exact write (guarded, idempotent):**
  `UPDATE mace_rung SET status='open' WHERE rung_id='mace-XLE-2026-10-30-60-59-70-71-20260917' AND status='closing';`
  Touch NOTHING else — `exit_ts/exit_reason/exit_debit/realized_pnl` are ALREADY NULL (verified in the
  diagnosis); leave them NULL = the true "never closed" state (no half-set exit fields). `pt_order_id` is
  synthetic (NULL); `pt_debit` stays 0.15; the next manage tick re-derives the synthetic PT and re-evaluates
  through the new guard.
- **Reconciliation to RH:** the position was NEVER closed (0 `mace_exit_fill`; empty-response blocked all 35
  attempts; Jack confirmed 8 leg-contracts intact / true value ~0.41). So `status='open'` makes the DB match
  reality — a pure record correction, no RH-side action.
- **Mark-state check before the reset (RO proxy; per-leg live re-quote is broker/classifier-blocked):**
  re-run the RO diagnosis to confirm (a) the rung's `mace_rung_live` mark (frozen 0.13 while CLOSING) vs the
  10/16 sibling's fresh mark, and (b) whether the journal still shows `empty response` on those 2026-10-30
  contracts. If the bad quote persists, that is EXPECTED and SAFE: post-reset the guard rejects the 0.13
  (sibling ~0.20) and the rung HOLDS + alerts instead of re-firing the false PT.
- **Sequencing (load-bearing):** reset MUST come AFTER the guard is deployed + boot-verified. Guard-first
  means a still-bad Monday 0.13 -> guard rejects -> HOLD; reset-first (no guard) -> PT re-fires on 0.13 ->
  bounces back to CLOSING.

---

## 6. Weekend execution order (single agent, one branch, box-only)

1. **Verify base** (0 above): RO box md5/sha re-read; confirm branch base == deployed trigmid build
   (manager `a83b62e1` / execution `a65955bb` / config `bfde856f`).
2. **Build** the guard on one branch off `2362db46`: the `management.mark_guard` config block +
   `config.py` parse/validate + `strategy.py` assessor/sibling helper + `manager.py` wiring +
   `execution.py` `get_live_state`. Finalize `config/mace.yaml` and RECORD its new `sha256sum` (the expected
   post-deploy `config_hash`) into the deploy manifest. Commit incrementally.
3. **Box-scratch test** (RO runner, `-p no:pytest_ethereum`): new guard tests + full suite green + import
   closure. 3-way prove intent: box blobs (5 files) == build; record the new config sha256.
4. **Deploy box-only (RESERVED — Jack):** MULTI-FILE graft (config/mace.yaml + config.py + strategy.py +
   manager.py + execution.py) via scp+tar staging (drift-gated box==base for all 5, backup, py_compile,
   partial-graft rollback) + the canonical `restart_tc.ps1` (**restart REQUIRED** — config is read once at
   boot). Jack authorizes + runs. **`config_hash` MOVES** bfde856f -> recorded new value. NO FF-push (git
   divergence; reconcile deferred).
5. **Boot-verify (RO):** PID re-read, **`config_hash != bfde856f` AND == the recorded new sha256[:12]**
   (config edit took effect), MACE wired OK (the mark_guard block parsed — else fail-fast abort), 0 import
   errors/tracebacks, all divisions back, 4 MACE loops online, guard blobs present, arm state unchanged.
6. **DB reset (RESERVED — Jack):** after boot-verify, present the guarded one-line UPDATE runner + a backup;
   Jack runs it. RO-verify: `status='open'`, exit fields still NULL, and on the next manage tick the guard
   HOLDS (no PT re-fire; `mace_pt_mark_reject` alert) if RH still serves the bad quote — OR PT fires on a
   sane mark if RH recovered.
7. **Verify before Monday open:** rung is `open` + healthy (42 DTE, defined risk); guard behaving (hold+alert
   on bad quote, or clean sane PT). Report.

---

## Verification (end-to-end)

- Unit: `pytest tests/test_mace_strategy_manage.py tests/test_mace_mark_guard.py -p no:pytest_ethereum` (box)
  — the 9 cases above pass; existing precedence tests unchanged.
- Full suite green (box-scratch, `-p no:pytest_ethereum`), 0 new failures.
- Post-deploy boot-verify RO runner: `config_hash != bfde856f` AND == the recorded new `sha256[:12]`,
  MACE wired OK (block parsed), 0 mace exceptions, divisions reconciled.
- Post-reset RO runner: `mace_rung` XLE 2026-10-30 `status='open'`; next-tick audit shows either
  `mace_pt_mark_reject` (RH still bad -> correct HOLD) or a sane PT decision (RH recovered).

## Decisions (confirmed by Jack 2026-09-18)
1. **Branch base = `2362db46`** (deployed trigmid box-truth), NOT c2593e34; pre-build md5 re-verify base ==
   running box.
2. **Constants in `config/mace.yaml`** (`management.mark_guard`): accepted `config_hash` MOVES off
   `bfde856f`; deploy restarts to re-read; boot-verify treats config_hash CHANGED (to the recorded new
   sha256) as CORRECT.
3. Guard = PT-only, TIMELY + SANE (sibling-compare primary, defined standalone fallback), untrusted -> HOLD
   + reuse Activity Pulse + Telegram alert. NO volume gate. Deferral/hold/CLOSING logic UNCHANGED.
4. DB reset CLOSING->open sequenced AFTER the guard deploys; RO mark-check on the 60/59/70/71 2026-10-30
   contracts before the reset. MACE-scoped, one branch, box-only, no FF-push. Single agent owns
   build -> deploy -> reset in order.
