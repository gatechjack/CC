# DRAFT deploy_log entry — MACE OQ-2 + 3-active + halt button

Fill the `<...>` placeholders at Phase 5 (deploy morning), then append to
`runbooks/deploy_log.md` and commit on prod-live. Roster line below assumes the
full-3 outcome — adjust per the Board's live-quote pick (pre-ruling: two slots
out → IWM FIRST then FXI; one slot out → FXI first, liveness-gated).

---

## 2026-08-14 <HH:MM> UTC — MACE OQ-2 entry serialization + 3-active universe (IBIT/XLE/GDX) + /mace entry-halt button (frozen config + code, RESTART)

**Commits:** ee9cfd5, 66cad59, 7300985, 3210a4a, <phase3-config-commit>, <memo-commit> (branch `claude-2026-08-13b`, base `b11af9b`)
**Triggered by:** Board 2026-08-13 timeline override — 3-symbol live at the 2026-08-14 15:45 ET eval (attended). Checkpoint 0 ratified: GDX Option A (PROJECTED 12/21 guard-ON + December tripwire), ex-div citations incl. IWM 9/15+12/15 correction, widths/blackouts, roster IBIT+XLE+GDX backfill FXI→IWM (two-out flip: IWM first).
**Board memo:** `planning/mace_3active_oq2_board_memo_2026-08-13.md`
**Backup tag:** `<mace_oq2_bak_20260814/...>`

**Files deployed (8):**
- `trading_corp/mace/manager.py` — OQ-2 ordering (IVR-desc primaries then overflow) + per-symbol dynamic deadline + `mace_entry_window_skip` audit + per-symbol/round halt checks (LF-md5 `<pre>` → `<post>`)
- `trading_corp/mace/execution.py` — `run_entry(deadline=, halt_fn=)`; per-attempt effective cutoff `min(cutoff, deadline)`; stand-down reasons `window_budget`/`operator_halt` (precedence cutoff > operator_halt > window_budget) (LF-md5 `<pre>` → `<post>`)
- `trading_corp/mace/loops.py` — slots-loop poll 30s → 5s (LF-md5 `<pre>` → `<post>`)
- `trading_corp/web/mace_view.py` — POST /mace/halt + /mace/arm (audit-before-state), tri-state wiring (LF-md5 `<pre>` → `<post>`)
- `trading_corp/web/templates/mace_live.html` — halt partial include (LF-md5 `<pre>` → `<post>`)
- `trading_corp/web/templates/partials/mace_halt.html` — NEW tri-state pill + button (LF-md5 n/a → `<post>`)
- `config/mace.yaml` — universe [IBIT, XLE, GDX]; SPY+GLD enabled:false; new XLE/GDX/IWM blocks; FXI w1 no-fallback; IBIT overflow_only removed + guard:false (non-payer); rung_risk_pct 0.10 / deployment 0.95 / band-max 260 / weekly 1 / attempts 2 / wait 30 (LF-md5 `<pre>` → `<post>`; config_hash `fe177fcd3882` → `<new>`)
- `config/ex_dividend_calendar.yaml` — XLE 2 confirmed (SSGA SPD003792); GDX PROJECTED 12/21 + tripwire comment; IWM 5 confirmed incl. 9/15+12/15 corrections + 12/30 excise (iShares GPS0826-5839861); FXI 12/15+12/30 confirmed (LF-md5 `<pre>` → `<post>`)

**Features shipped:**
- OQ-2 serialization: N-symbol entry round fits the 15:45→15:58 window by construction — IVR-desc order, dynamic per-symbol deadline (donation forward), audited `mace_entry_window_skip`, `window_budget` clean stand-down. One ladder in flight, ever.
- 3-active universe: IBIT (w1, IVR floor may legitimately skip day 1), XLE (w2/w1, OPEC blackout, live Sep ex-div guard), GDX (w2/w1, FOMC blackout, PROJECTED ex-div + tripwire). SPY+GLD retired to enabled:false — SPY's 2 open W33 rungs remain fully managed (manage/exit/reconcile never read `enabled`); GLD 0 open rungs verified below.
- /mace entry-halt button: latch (agent_state robinhood_mace/entry_halt), auto_execute:false semantics, halts next symbol/attempt, manage loop untouched, tri-state ARMED / HALTED (button) / HALTED (config), fail-safe read (error == NOT halted).
- Board ladder params: entry 2×30s (~70-80s/symbol typical, worst ~130s).

**Verification:**
- Targeted MACE suite 202/202 green (14 files) at build; full suite `<PASS/FAIL counts>` == 88f/12e baseline, 0 new MACE failures (junit `cc\<name>.xml`).
- Morning intraday shadow-eval: `<per-symbol 0.30×width floor results + Board roster pick>`
- Drift-gate: `<all touched runtime files LF-md5 == b11af9b blobs pre-swap>`
- Restart (az run-command root, RG-SHARED-PROD/tc-prod-vm, `<HH:MM>` ET — outside 15:40-15:58, ≤13:00 ET): MainPID `<old>` → `<new>`, NRestarts=0, 0 tracebacks.
- Boot verify: config_hash `<new>` logged; /mace 200 → 3 actives + tri-state + halt button live-tested (ARM→HALT→ARM latch cycle); 4 loops online; SPY 2 rungs visible+managed; **GLD = 0 open rungs**; PEAD/PMCC/bitunix/kalshi healthy; web :8000.
- 15:45 ET attended eval: `<mace_entry_eval per active; entries IVR order; no dup ref_ids; terminal by ~15:59:35>`

**Rollback recipe:** restore `<backup dir>` (6 code files + 2 yaml `.bak`) + restart outside the window. Config-only rollback: revert universe/params + restart. Kill-switches: auto_execute:false (hot), standby:true (hot), --live-divisions removal + restart, UI halt button.

**prod-live:** `b11af9b` → `<new tip>` (FF, same session).
