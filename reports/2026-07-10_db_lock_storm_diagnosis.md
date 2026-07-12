# Shared-DB SQLite Lock Storm — Read-Only Diagnosis (2026-07-10 ~14:34–14:35 UTC)

Investigator: Claude Code · Mode: **read-only, propose-don't-apply** · Prod DB: `/home/azureuser/trading_corp/data/trading_corp.db` (1.5 GB, WAL)
No DB writes, no code changes, no PRAGMA changes, no restart, no PCT change were made. All timestamps UTC.

**Current health (as of 15:34):** engine restarted cleanly at 15:21:48 (deliberate, `NRestarts=0`, not a crash); **0 lock errors since the restart**. No runaway holder right now → **no URGENT stop condition**. One standing correctness hole persists (see §2).

---

## §1 — PEAD trade correctness verdict: **YES, recorded correctly. Airtight.**

The real trade this morning was **PEP buy, 1.71 sh @ 136.615, execution_mode=live**, order `2e0f1aca…`, filled **13:31:13 UTC (~1h BEFORE the storm)**.

| Table | Row | Verdict |
|---|---|---|
| `proposed_order` | 2e0f1aca… PEP buy · **status=filled** · fill 136.615 @ 13:31:13 · live | ✓ present, consistent |
| `paper_trade_record` | 2e0f1aca… PEP buy · **qty 1.71 @ 136.615** · live | ✓ fill persisted |
| `pending_order` | (empty for PEAD) | ✓ promoted + deleted cleanly, no orphan |

- Journal grep for order `2e0f1aca` over 24h: **zero** lock / retry / fallback lines. PEAD's write path was uncontended.
- **Correction to the incident report:** the `log_proposed_order` that retry-exhausted at 14:34:42–58 was order **2581415e = robinhood_pmcc CIFR (paper, board_rejected)** — *not* PEAD. All five `log_proposed_order` exhaustions in the last 24h were `robinhood_pmcc` paper board-rejected orders (14896aa4/13c6785c/8a977908 on 7/9; 0e606505/2581415e on 7/10). None was PEAD; none was a real-money order.
- Even those 5 PMCC rows still carry the correct final `board_rejected` status (INSERT-OR-REPLACE landed via other lifecycle writes), so the drop cost only an intermediate status write, not the row.

**PEAD is safe. No customer-facing correctness gap on the real trade.**

---

## §2 — File-fallback audit events: **91 total, spanning 2026-06-01 → today, NONE reconciled. This is the live correctness hole.**

Fallback file: `data/audit_event_write_failed.jsonl` (46 KB, 91 lines, mtime 14:35:19 today).

- A drain tool **exists**: `scripts/replay_audit_event_write_failed.py` (idempotent by `(ts,actor,kind,payload_json)`, archives file to `.replayed-<iso>` on full drain).
- It is **NOT scheduled** anywhere (no systemd timer in `infra/`, not in cron) and the file is **un-archived with entries back to June 1** → it has **never been run in prod**. All 91 events are currently **absent from `audit_event`**.
- Downstream impact: any reader of `audit_event` (dashboards, `events_since`/`recent_events`, resolvers filtering on `(actor,kind)`, EOD review) is missing these 91 events.
- **Note:** `tc-audit-reality.service` is in `failed` state but is a **red herring** — it's a bitunix SFP *recorded-vs-simulated R-multiple* reconciler that exits 1 on any mismatch by design; it does **not** reconcile this file.

Fallback composition (all 91):

| count | actor / kind | notes |
|---|---|---|
| 30 | board / board_rejected | the known anti-pattern; approval-flow audit lost |
| 22 | hitl / board_decision_received | approval-flow |
| 14 | kalshi_llm_arbitrage / kalshi_llm_probability_called | |
| 8 | hitl / pending_approval_added | |
| 7 | risk / risk_approved | |
| 3 | scheduler / scheduled_scan_error | |
| 2 | risk / risk_rejected | |
| 1 | **data_exec / filled** | robinhood_pmcc **CIFR paper** fill (7/8) — paper, not real money |
| 1 | **board / board_approved** | order bc0b26ee "via web" (7/8) |
| 1 each | bitunix_futures/live_order_rejected, kalshi_llm_scan_cycle, polymarket_scan_cycle | |

Today's storm added only **2** file entries (both `board_rejected`, at 13:34:34 and 14:35:19). The 1 `data_exec/filled` and 1 `board_approved` are from 7/8, and the `filled` one is **paper** (no real-money fill was ever lost to file).

**Reconciliation status: 0 of 91 reconciled.** Fix is cheap (run the existing script; then schedule it).

---

## §3 — Root-cause: lock-holder identification

**The write lock was contended/held continuously for ~45 seconds (14:34:42 → 14:35:27).** Timeline:

```
14:34:37  hitl/board_decision_received e2770d10 (CIFR) reject "approval timeout" (source=timeout)
14:34:37  board/board_rejected         e2770d10  ✓ landed
14:34:37  hitl/board_decision_received 2581415e (CIFR) reject "approval timeout"
14:34:42  log_proposed_order 2581415e  LOCKED attempt 1/4  (starvation begins)
14:34:47  attempt 2/4    (+5.13s wall — full busy_timeout elapsed, sleep was only 0.16s)
14:34:52  attempt 3/4    (+5.17s)
14:34:58  log_proposed_order FAILED after 4 (~16s) — NOT raising
14:35:03  log_event board/board_rejected LOCKED attempt 1/4
14:35:08  attempt 2/4
14:35:14  attempt 3/4
14:35:19  log_event board/board_rejected FAILED after 4 → fallback file
14:35:27  Polymarket copy trader run_scan_cycle FAILED: database is locked (uncaught Traceback,
          _save_whale_state → set_agent_state → db.py:556)  [VICTIM, not cause]
```

**Key quantitative tell:** each retry's wall-clock gap is ~5.1 s while the coded sleep is 0.12–0.78 s → **the full `busy_timeout=5000ms` elapsed on every attempt without acquiring the lock.** During the entire 14:34:42–14:35:19 starvation, **not a single `[audit]` write succeeded** → the holder is a writer that emits no audit line.

**Trigger:** at 14:34:37 two PMCC CIFR orders hit **approval-timeout**, which **resumes their suspended LangGraph threads** (interrupt/resume). The concurrent RKLB roll pair (938922d9 BUY-TO-CLOSE, abea2612 SELL-TO-OPEN) enters the same graph at 14:35:22–27. The PMCC roll scan evaluates many candidates per cycle, driving a burst of graph transitions.

### Top-3 lock-holder hypotheses (ranked by evidence)

**H1 (strongest) — the LangGraph `AsyncSqliteSaver` checkpointer, driven by the PMCC approval-timeout resume + roll burst, is the hot writer that starved the shared-DB app writers.**
- Evidence: victims are all approval/execution-flow writes for PMCC orders; the burst is a PMCC roll cycle; the checkpointer writes to `checkpoints`/`writes` tables (no `[audit]` line — matches the "silent holder" observation); it opens its **own** connection to the same file (separate WAL writer); it is a **documented** prior source of `database is locked` (`pmcc_approval_reconciler.py:9`, `main.py:1097`); `checkpointer.py:19` uses `AsyncSqliteSaver.from_conn_string` with **no tuned busy_timeout / WAL / retry**.

**H2 — SQLite writer *unfairness / sustained hot-writer starvation* (not necessarily one giant transaction).** The 30-day trend shows **contention == exhausted every single day (100% exhaustion, 0% retry-save)** → holds are *always* > the ~20 s retry budget. This is the signature of one high-frequency writer (H1's checkpointer, or the PMCC multi-candidate write stream) continuously re-grabbing the write lock and starving the low-frequency victims, because SQLite's busy-handler has no FIFO fairness. A single 45 s transaction is possible but less likely than sustained re-acquisition.

**H3 — fsync amplification (`synchronous=FULL` on a 1.5 GB DB on Azure managed disk).** Every commit fsyncs; under the PMCC burst, elongated fsync latency stretches each write's hold, deepening the queue. Supporting but secondary (no direct disk-latency evidence); acts as a multiplier on H1/H2 rather than a standalone cause.

**Amplifier (applies to all):** `log_event`/`log_proposed_order` retry with a **blocking `time.sleep`**. If invoked on the asyncio event loop (the approval/execution path is async), the loop **freezes for ~16 s per victim** — the retry mechanism itself propagates the stall system-wide.

---

## §4 — SQLite configuration verdict (the load-bearing answer)

| Setting | Value | Source | Verdict |
|---|---|---|---|
| `journal_mode` | **WAL** | DB header (`-wal`+`-shm` present, live) + `db.py:522` | ✓ Already WAL. **Refutes the "probably not WAL" hypothesis.** Concurrent readers are fine; the problem is *writer-vs-writer*. |
| `busy_timeout` | **5000 ms (app)** | `db.py:523` (CLI's `0` is just the fresh-CLI default, not the app) | Too low vs observed ~45 s holds → 100% exhaustion. |
| `synchronous` | **FULL (2)** | `db.py` default (not overridden) | Every commit fsyncs; on a 1.5 GB DB this lengthens holds. WAL+NORMAL would be safe and ~2× cheaper. |
| Connection lifecycle | **fresh connection per operation**, autocommit (`isolation_level=None`), `check_same_thread=False`, closed on context exit (`db.py:516-527`) | — | Good: no long-held transactions *in the app layer*. Each `execute()` is its own implicit txn. |
| Transaction scope | **short/implicit** in the app layer; **untuned** in the checkpointer | `db.py` vs `checkpointer.py:19` | The contention is **cross-connection** (app pool vs checkpointer), not app-layer long transactions. |
| Checkpointer connection | `AsyncSqliteSaver.from_conn_string(db_path)` — **own connection, no explicit WAL/busy_timeout/retry** | `checkpointer.py:9-20` | The untuned second writer on the shared file. |
| Retry layer | 4 attempts, base delays (0.1, 0.3, 0.7)s + jitter, on top of 5 s busy_timeout ≈ **~20 s total budget** | `logger.py:26`, `db.py:29` | **0% save rate in 40 days** — the budget is shorter than the holds, so it only *delays* failures ~20 s (and freezes the loop while doing so). |

**Verdict:** WAL is already on; the load-bearing problems are (a) a **second, untuned writer (the checkpointer)** on the shared file, (b) **busy_timeout (5 s) far below real hold durations**, (c) **synchronous=FULL** lengthening holds, and (d) a **blocking retry** that stalls the event loop. This is a *writer-serialization + hot-writer-starvation* problem, not a journal-mode problem.

---

## §5 — PCT write volume vs on-hold baseline

PCT (`polymarket_copy_trader`) is **on hold** (paper, not in `--live-divisions`; the pct-pruner + watchlist systemd services are inactive/dead). It is, however, **running the full paper scan+propose+resolve loop**, not just minimal bookkeeping:

| Signal (last 24h) | Volume | Rate |
|---|---|---|
| `audit_event` would_have_placed | **184** | peaks ~21/hr during US market hours, ~1/hr off-hours |
| audit_event copy_order_rejected_by_risk / entry_skipped_drift | 9 / 5 | |
| `agent_state` whale-state keys (upserted per scan cycle) | **67 keys**, newest 15:36 | rewritten each cycle |
| `polymarket_round_trips` (division=polymarket_copy_trading) resolved | **149** | resolver bookkeeping |

**Verdict:** **NOT customer-facing rates** (peak ~21/hr, not thousands/hr) and **no real order flow** (all paper `would_have_placed`, execution_mode=paper). BUT it is doing **more than "background bookkeeping" implies**: it runs the complete whale-copy simulation every cycle (~180 paper proposals/day + 67 whale-state upserts/cycle + ~150 round-trip resolutions/day, ≈ 350+ writes/day). Two consequences worth knowing:
1. PCT's `set_agent_state` whale-state write has **no retry** (see §7) → PCT is the **guaranteed first casualty** of every storm and throws an **uncaught traceback** (caught only at the loop wrapper `main.py:4215`). The trend's `pct_tb` column corroborates: PCT tracebacks spike on the big contention days (7/8: 5, 6/18: 5, 7/9: 3).
2. An on-hold division is still contributing meaningfully to shared-DB write pressure.

*(No PCT change made or recommended here — PCT stays on hold independently of this investigation.)*

---

## §6 — Trend + severity: **established recurring pattern, ~40 days, trending up. Severity: EVENTUAL (fix soon, not this-second).**

Per-day lock events from the journal (retention 2026-05-24 → now). `contention` = writes that hit the lock; `exhausted` = writes that gave up after 4 retries; `pct_tb` = PCT uncaught tracebacks:

```
2026-06-01  c=1  ex=1     2026-06-22  c=7  ex=7  tb=1     2026-07-03  c=3  ex=3
2026-06-02  c=2  ex=2     2026-06-23  c=5  ex=5  tb=2     2026-07-06  c=3  ex=3
2026-06-03  c=1  ex=1     2026-06-25  c=9  ex=9  tb=1     2026-07-07  c=7  ex=7  tb=1
2026-06-05  c=2  ex=2 tb=2 2026-06-26 c=2 ex=2            2026-07-08  c=18 ex=18 tb=5  <- worst
2026-06-10  c=15 ex=15 tb=1 2026-06-29 c=2 ex=2           2026-07-09  c=11 ex=11 tb=3
2026-06-12  c=1  ex=1     2026-06-30  c=10 ex=10 tb=1     2026-07-10  c=4  ex=4  tb=1 (partial day)
2026-06-16  c=1  ex=1 tb=2 2026-07-01 c=2  ex=2  tb=1
2026-06-18  c=12 ex=12 tb=5 2026-07-02 c=4 ex=4  tb=2
2026-06-19  c=10 ex=10   2026-07-03 ...
```

- **Not a first-time event, not rare** — present every day for 40 days.
- **contention == exhausted on every day** → the retry layer has a **0% real save-rate**; holds are always > the ~20 s budget.
- **Trending up**: the two worst days are the two most recent big ones (7/8 = 18, 7/9 = 11). The PMCC lifecycle reconciler + boot-recovery landed 7/8 (per project memory); worth checking whether that raised approval-flow/checkpoint write pressure — the worst day is the deploy day.
- **Severity = EVENTUAL, not one-off, not currently-firing.** No real trade has been lost (PEAD clean; PMCC drops are paper and eventually-consistent). The realized cost so far is (a) 91 lost audit events and (b) periodic PCT tracebacks. Fix is warranted soon; not an emergency.

---

## §7 — Blast radius: per-division write behavior on lock

Four buckets: **A** retry-loud (raises → visible), **B** retry-silent (swallows after retry), **C** file-fallback (silent hole unless drained), **D** drop/no-retry (raises uncaught OR bare-except swallow). From static code survey + prod evidence:

| Bucket | Writer / call site | Table | On lock |
|---|---|---|---|
| **A** | `insert_paper_trade_record` (`db.py:591`) | paper_trade_record | retry 4× → **re-raises** (visible; caller handles). This is why the PEAD fill is safe. |
| **B** | `log_proposed_order` (`logger.py:132`) | proposed_order | retry 4× → **silent return** (logged error, no raise). PMCC's 5 drops today/yesterday. Eventually-consistent via other lifecycle writes. |
| **B** | strategy `set_agent_state` wrapped in bare `except` (bitunix_sfp_observer, bitunix_futures_observer, polymarket_whale_analyst) | agent_state | swallowed (observe/heartbeat state — low stakes) |
| **C** | `log_event` (`logger.py:63` → `_write_audit_fallback:29`) | audit_event | retry 4× → **file `audit_event_write_failed.jsonl`**, returns None. **Unreconciled (§2).** board_rejected / risk_approved / filled all ride this path. |
| **D** | **`set_agent_state` / `delete_agent_state` (`db.py:541/660`) — NO retry** | agent_state | **raises on first collision.** PCT `_save_whale_state` is unguarded → uncaught traceback (14:35:27). Others (`lord_otter`, `market_cypher`, `kalshi_llm_arbitrage`, caches) wrap in bare `except` → silent drop. |
| **D** | `log_brief` (`logger.py:182`), `_persist_combo_positions` (`data_exec.py`, executemany), `pead_strategy` pending-order DELETE | daily_brief / position / pending_order | direct `db.connect()` write, **no retry** → raises on lock |
| **D** | LangGraph checkpointer (`checkpointer.py:19`, resume at `main.py:1097`) | checkpoints/writes | **no retry/timeout tuning**; resume-under-lock has historically stranded PMCC threads (mitigated by the 7/8 boot-recovery reconciler) |

**Silent-hole / worst-case sites:**
- **C (file-fallback):** only `log_event` (audit_event). Not replicated elsewhere — good, it's one path, but it's **unreconciled**.
- **D (no-retry):** `set_agent_state`/`delete_agent_state` are the biggest gap — used 15+ places; either raise uncaught (PCT) or bare-except-swallow (lord_otter, market_cypher, kalshi_llm, position/whale caches). These **lose state silently** and, unlike audit_event, have **no fallback file** — a true silent drop.
- The **checkpointer** has no retry; its failure historically strands approval threads (now caught by boot-recovery).

*(File:line citations for strategy-level bare-except sites are from the code survey; the core helper behavior — `db.py:541/660` no-retry, `logger.py` file/silent, `db.py:591` re-raise — is directly verified.)*

---

## §8 — Ranked fix candidates (proposals only — nothing applied)

Ranked by cost/benefit. **[C]** = configurational (PRAGMA/settings/ops), **[A]** = architectural (code). "Restart" = needs the engine bounced to take effect.

| # | Fix | Type | Restart? | Fixes | Doesn't fix | Cost |
|---|---|---|---|---|---|---|
| **1** | **Drain the 91 backlog now with the existing `replay_audit_event_write_failed.py`, then schedule it as a systemd timer** (mirror `tc-audit-reality.timer`) | [C]/ops | **No** (separate oneshot) | The §2 silent hole — recovers 91 events + auto-recovers future ones | Contention itself (data-loss mitigation only) | **Tiny** — script exists, idempotent; add one `.timer` |
| **2** | **`synchronous=NORMAL` + raise `busy_timeout` to ~15–30 s** in `db.py connect()` (and mirror on the checkpointer) | [C] (1-line each) | Yes | Shortens write holds (~2× cheaper commits) + lets contended writes actually wait out a hold → cuts the 100% exhaustion rate | Doesn't remove the hot writer; long holds >30 s still fail | **Low** |
| **3** | **Move the LangGraph checkpointer to its OWN SQLite file** (dedicated `checkpoints.db`) | [A] | Yes | Removes the #1 competing writer (H1) from the shared file — the checkpointer and app stop contending entirely. Precedent exists: research-firm graph already avoids sharing the CEO saver (`main.py:1108`). | Doesn't help app-vs-app contention (smaller); needs boot-recovery/reconciler path repointed | **Medium** |
| **4** | **Add the retry wrapper to `set_agent_state`/`delete_agent_state`** (mirror `log_event`) | [A] (small) | Yes | Converts the bucket-D no-retry sites (incl. PCT) from uncaught-traceback / silent-drop to survivable | Not the root contention; also consider a fallback for lost state | **Low** |
| **5** | **Offload blocking DB writes off the event loop** (`asyncio.to_thread`/executor for `log_event`/`log_proposed_order` when async) | [A] | Yes | Stops the retry `time.sleep` from freezing the whole engine ~16 s per victim | Not the holder itself (removes the amplifier) | **Medium** |
| **6** | **Single-writer queue / one shared serialized writer** for the shared DB | [A] | Yes | Definitive: eliminates cross-connection writer contention structurally | Large refactor; overkill if 1–3 suffice | **High** |
| **7** | **Reduce PMCC scan write amplification** (batch multi-candidate approval writes / lower checkpoint churn) | [A] | Yes | Attacks the trigger volume (the 7/8 spike day = deploy day) | Narrow to PMCC; other bursts remain | **Medium** |

**Recommended sequence (for operator decision, not yet applied):** **#1 immediately** (no restart, closes the data hole, drains the 91 backlog) → **#2** on the next planned restart (cheap, high-yield config) → **#3 + #4** as the durable architectural pair (checkpointer isolation + agent_state retry) → reassess before #5–#7.

### Data-gathering gaps / caveats
- `sudo journalctl` requires a password in the automated ssh session; unprivileged `journalctl -u trading-corp` **does** work, so the journal evidence above is complete for the unit. No `PRAGMA`/disk-latency probe was run (would need a live connection / sudo) — the fsync (H3) claim is inferred, not measured.
- Strategy-level bare-except file:line citations come from the static survey; core-helper behavior is directly verified against source.
