# Two-state model audit — TRADING | HALTED-INERT (read-only map + collapse plan, 2026-06-27)

Operator model: a division is either **TRADING** (live, real orders) or **HALTED-INERT** (loaded, off,
does nothing — no orders, no data-fetch, no replay/sim — ready to flip live). "Paper" as an active
fill-sim/replay engine should not exist. Map current behavior; change nothing.

*Sources: core machinery read from the main checkout + corroborated by LIVE prod `audit_event`; SFP
specifics from the deployed SFP branch (worktree). Architecture is stable/shared; exact line numbers may
differ slightly on prod.*

## TL;DR — the core finding
- **"Paper" today is an ACTIVE simulation, not inert:** a non-live division scores → logs
  `would_have_placed` → writes a `paper_trade_record` row → the **global replay loop re-walks historical
  klines every 15 min** (+ a boot catch-up) to classify it win/loss.
- **`paper_trade_record` is the UNIVERSAL trade ledger** — paper *and* live. Live SFP writes to it
  (`execution_mode="live"`) and the **reconciler reads it to track live positions**. It cannot be removed.
- **The replay loop is the dominant, bot-like kline footprint** (~10× the live bar-cache; paginated
  historical; big boot bursts) — and the operator's flag-trigger.
- **No clean inert state exists.** `enabled:false`=unloaded, not-in-`--live-divisions`=active paper sim,
  `standby`=cosmetic for bitunix, `auto_execute:false`=still scores+writes paper. The bar-cache and
  replay are **global, unconditional tasks** — no division flag stops them.

## Q1 — What a non-live ("paper") division ACTIVELY does
Per signal/bar-tick (e.g. bitunix_futures, currently paper — confirmed live in `audit_event`):
1. Observer loop **scores + PA-validates** (`bitunix_score_decided`, `pa_validation_decision`).
2. On approve → logs `would_have_placed` + `db.insert_paper_trade_record(...)` — a `paper_trade_record`
   row (the sim seed). [observer ~2776-2792; main.py per-division blocks]
3. Consumes the **global** bitunix bar caches (it does not fetch klines itself).
4. The **global replay loop** (`start_replay_loop`, 15 min + boot catch-up, main.py:1598/1557) loads every
   pending `paper_trade_record` (result IS NULL) and **fetches historical klines** (paginated `1m`,
   ~8 windows/row, re-walked each tick for up to the 24 h max_hold) to classify win/loss/expired.
5. Telegram notify on resolution.
→ It scores, writes paper records, and **drives the repetitive historical kline fetch**. Fully active.

## Q2 — Does a HALTED-INERT state exist? NO. (minimal change below)
| Flag | Effect | Inert? |
|---|---|---|
| `enabled:false` | division **not loaded at all** (`load_divisions` filters it) | No — unloaded, not "ready to flip" |
| not in `--live-divisions` | `is_live_division=False` → PaperExecutionBroker + `would_have_placed` | No — **active paper sim** |
| `standby:true` | **cosmetic for bitunix** (UI badge only; consumed only by tasty/robinhood observers, NOT the bitunix order path) | No |
| `auto_execute:false` | blocks LIVE placement; paper/disarmed division **still scores + writes paper records** | No |
| halt | stops trading; **data + replay continue** | No |
- And the **global tasks** (bar-cache poll, replay loop) are unconditional — not gated by any division state.
**Minimal change for "halted = inert":** (a) a real inert gate that **short-circuits the observer's
signal handler before scoring/`would_have_placed`** (repurpose `standby` for all families, or add
`mode: trading|halted`); (b) **gate the global tasks** to run only when ≥1 bitunix division is LIVE;
(c) with inert divisions writing no paper rows, replay's classification work drains to zero on its own.

## Q3 — FUTURES dependency (the critical one)
- **bitunix_futures has NO live account** — its only active output is the paper sim (`would_have_placed`
  + replay classification). It has **no functional dependency** on the sim; making it inert breaks
  nothing for trading (it isn't trading). Only loss = futures' paper analytics panels.
- **SFP (live) does NOT depend on the replay bar-walk:** its exits are **venue-side** (B1 stop +
  `place_tpsl_order` TP leg) and resolution is **reconciler-driven**. SFP uses `paper_trade_record`
  only as its **live ledger**.
- Shared machinery that MUST stay for live SFP: the `paper_trade_record` **table**, the **reconciler**
  (reads live rows, books resolutions, halt safety), and the **live bar-cache** (15 m detection + HTF gates).
→ **Futures can sit HALTED-INERT cleanly.** Replay can stop doing historical classification. The table +
reconciler + bar-cache remain for SFP.

## Q4 — What reads paper-simulation output
- **Dashboard (`web/data.py`)**: win-rate panel (paper unbounded + live epoch-scoped split), "recent
  fires" panel (`paper_trade_record WHERE division='bitunix_futures'`), tier/result sim-P&L aggregates.
  → Inert futures: these **freeze/empty**. Handle: render a HALTED-INERT state, not stale paper stats.
- **Reconciler** (`_load_tracked_live_rows`): reads **LIVE-tagged** `paper_trade_record` rows — serves
  LIVE divisions (SFP). **Unaffected** by inert futures (which writes no live rows). **Must not break.**
- **Replay**: classifies paper rows (→ 0 when inert) + a dormant live-exit fork (futures Path-C).
- **Telegram lifecycle notifier**: pings on paper resolution → goes quiet for inert divisions (fine).
→ Only the **paper analytics panels** stop updating (intended). Live tracking untouched; nothing breaks.

## Q5 — Footprint confirmation
BitUnix kline API sources (both **global, unconditional**):
- **(a) live bar-cache polls** — 3m/60s (~1,440/day) + h1/5m + h4/15m + d1/30m + funding/30m ≈ **~1,900
  calls/day**. Recent-window, single-call, **benign** client pattern. **Needed for live SFP.**
- **(b) replay loop** — 15 min + boot catch-up, **paginated historical** (~8 windows/pending row,
  re-walked up to ~96×/trade over 24 h) → **~15–23k calls/day** with a normal backlog, **plus large boot
  bursts**. Historical + bursty = bot-like.
→ Replay is **~10× the live cache** and the bot-like pattern. **It is NOT the whole footprint** (the 60s
3m poll also recurs) but **it is the dominant, removable part.** Inert non-live divisions → (b) → ~0.
(a) remains as the benign live floor (HTF already on sane cadences; only the 3m 60s poll is notable).

## PLAN — collapse to TRADING | HALTED-INERT (code retained, not deleted)
1. **One state flag.** A division is LIVE (in `--live-divisions`; real broker; venue-side exits;
   reconciler) **or** INERT (loaded; observer short-circuits before scoring/paper-write; no broker order
   path; no `paper_trade_record` write). Repurpose `standby` to enforce this across families (today
   cosmetic for bitunix) or add `mode: trading|halted`.
2. **Enforce inert** at the top of each observer's signal/bar handler: if inert → return (no score, no
   `would_have_placed`, no `insert_paper_trade_record`). Paper-sim code retained, gated off.
3. **Gate the global tasks**: live bar-cache + replay start only when ≥1 bitunix division is LIVE. With
   futures inert + SFP live: caches stay (SFP needs them); **replay can be disabled** (SFP doesn't use
   it; futures writes nothing) or left idle (no pending rows).
4. **bitunix_futures → HALTED-INERT** (no account, no sim); config retained, flip-ready.
5. **Readers**: dashboard renders HALTED-INERT instead of stale paper stats; reconciler/live path
   untouched (serves SFP).
6. **Net after**: only the live bar-cache poll for live SFP (~1.9k/day, benign) + occasional
   live-position reconcile. The replay historical bulk + boot bursts (the flag-trigger) are **gone** →
   the new egress IP stays clean.

**Operator decisions (code retained either way):** (i) disable the replay loop entirely vs gate it;
(ii) repurpose `standby` vs add a new `mode` flag; (iii) full inert vs keep writing paper records for
any division you still want analytics on. This audit maps; you decide what to decommission.
