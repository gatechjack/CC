# Two-state collapse — READ-ONLY BUILD SCOPE (2026-06-27)

Scope of the **TRADING | HALTED-INERT** build (audit `1537de3`), step (a) of the IP-recovery plan.
Goal of the build: **drain the replay loop's ~15–23k/day historical-kline footprint** (the Cloudflare
bot-trigger on prod egress `20.51.145.253`) **before** the NAT-gw IP swap, so the new IP stays clean —
while keeping the LIVE SFP division byte-unchanged and fully fed.

**Status: READ-ONLY. No code changed. Deploy is operator-gated. One operator decision blocks the build (§4).**

All citations are against the worktree `bitunix-sfp-2026-06-25` @ `764b7ec` (branch
`bitunix-sfp-division-2026-06-25`, ahead origin/main 39). Prod line numbers may differ slightly; drift-gate
vs PROD md5 at deploy time as usual.

---

## 0. TL;DR
- **SFP byte-unchanged is structurally easy.** The two divisions are *entirely separate observer classes in
  separate files*. SFP's two files reference the futures observer **zero** times and the replay loop **zero**
  times (grep-proven). The build edits **futures + main.py wiring + strategies.yaml only**; SFP's files and the
  reconciler are **not touched** → md5-provable.
- **The drain target is ONE line: `main.py:1669` `start_replay_loop(...)`.** It is unconditional today, loads
  **all** pending `paper_trade_record` rows with **no division filter**, and paginates historical 1m klines
  (~8 calls/pending row/tick). A pre-existing backlog of `bitunix_futures` paper rows keeps it fetching **even
  after futures goes inert**.
- **★ THE FORK (blocks build):** the plan's stated gate — *"start replay only if ≥1 bitunix division in
  mode:trading"* — **does NOT drain replay**, because **SFP is trading**. That gate evaluates TRUE and replay
  keeps walking the futures backlog → IP stays flagged. Replay is the engine of the *paper-sim* state, which
  the two-state collapse **abolishes**; the correct move is to **disable replay** (or gate it on the existence
  of a *paper-sim* division, which under two-state is never). See §4 for the decision + collateral.

---

## 1. Structural separation — the SFP-byte-unchanged proof (GOOD NEWS)

| Concern | Finding | Cite |
|---|---|---|
| Separate classes/files | `BitunixFuturesObserver` (`bitunix_futures_observer.py`, 3668 L) vs `_BitunixSfpObserver` (`bitunix_sfp_observer.py`, 755 L) + `bitunix_sfp.py` (pure-python detector) | distinct files |
| SFP → futures coupling | grep `sfp` in futures observer = **none**; grep futures/`BitunixFuturesObserver` in SFP files = **none** | agent-proven |
| SFP → replay coupling | grep `paper_trade_replay`/`start_replay_loop`/`set_live_exit_executor` in SFP files = **none** | agent-proven |
| SFP bar feed | SFP reads its **own** dedicated 15m `LiveBarCache` dict (`bitunix_sfp_15m_caches`, `main.py:637-641`), NOT the shared futures 3m cache | `main.py:408-411,637-641` |
| SFP config | own `strategies.yaml` key `bitunix_sfp` (L1931-1949), own `auto_execute`/`execution_mode`, own hot kill-switch `_yaml_auto_execute()` keyed to `DIVISION="bitunix_sfp"` | `sfp_observer.py:59,673` |
| SFP exits | venue-side **B1 stop** (attached at `data_exec.place`, `sfp_observer.py:423`) + **`place_tpsl_order` TP leg** (`:498`); resolution by the **reconciler** | `sfp_observer.py:415-498` |
| Reconciler ⟂ replay | `bitunix_position_reconciler.py` imports only `db`, `models`, `bitunix_symbols` — **no** `paper_trade_replay`. Gated on `_recon_exec_mode=="live"` (`main.py:1005`), which is `live` because SFP is live → reconciler runs, books SFP, unaffected by replay being killed | `reconciler.py:31-45`; `main.py:1005,1794+` |

**Conclusion:** killing replay and adding an inert gate to futures cannot alter a single line SFP executes.
The byte-unchanged guarantee is enforced by **not editing** `bitunix_sfp_observer.py`, `bitunix_sfp.py`, or
`bitunix_position_reconciler.py` — md5 those three at deploy and prove equality vs PROD.

**Shared (read-only / additive) machinery SFP DOES use — must NOT regress:** `db.insert_paper_trade_record`
(universal ledger; SFP writes `execution_mode="live"` rows, `sfp_observer.py:444-449`), `PaperTradeRecord`/
`ProposedOrder` models, the `RiskAgent` singleton (shared with futures, injected), `bitunix_bracket.build_bracket_legs`.
None of these are edited by this build.

---

## 2. The build — three parts (code retained, gated, revertible)

### Part A — Futures inert gate (`bitunix_futures_observer.py` + `main.py` wiring + `strategies.yaml`)
Three entry paths reach scoring → `would_have_placed` → `insert_paper_trade_record`; **all must short-circuit**:
1. `observe_and_decide` (async, webhook signal entry) — `:676`; first exec line `:699`. **Guard before `:699`.**
2. `observe_alert` (sync; bias/CVD DB writes + classify audit) — `:651`, inner `:669`. **Guard before `:669`.**
3. `run_pa_redeem_loop` (background loop firing cached PA-rejected payloads) — `:1254`; fires at `:1279`.
   **Either guard before `:1279` or — cleaner — do not start this task when halted (`main.py:1770`).**

`insert_paper_trade_record` call sites in the file: `:2792` (paper) and `:3189` (live Path-C). Both sit
downstream of all three entries, so guarding the entries covers them.

Existing gates are insufficient: `auto_execute:false` only blocks `_place_live`; the paper write at `:2792`
**still runs**. There is **no** `halt/standby/enabled` guard at the top of either public entry today
(`:2764` `is_live` gate is downstream).

**Mechanism (recommended):** add `mode: trading | halted` to the `bitunix_futures` block in
`strategies.yaml` (mirrors the existing string-enum precedent `htf_gate.mode: off|shadow|enforce` `:1299`
and `execution_mode` `:1022`). `main.py` already reads the block for `execution_mode` (`:452`); read `mode`
the same way, pass `halted=(mode=="halted")` into the observer ctor, set `self._halted`, and:
- gate the three entry methods on `self._halted` (return immediately), and
- **skip starting `run_pa_redeem_loop`** at `main.py:1770` when halted.
Observer is still **constructed** (loaded, flip-ready, visible) — it just does nothing. Set
`bitunix_futures.mode: halted` in config.

Do **not** repurpose `standby`: it is UI-badge-only today (consumers: `divisions.py:53/159`,
`robinhood_joint.py:143`, `tasty_options.py:142`, `home.html:146`) with **no** order-path enforcement, so
repurposing it needs the *same* new guard code anyway **and** muddies an existing UI flag. A dedicated `mode`
is cleaner and revertible (flip back to `trading`).

### Part B — Replay drain (`main.py:1669`) — see §4 for the gate-condition decision
`replay_task = start_replay_loop(secrets.db_url, interval_sec=900)` is **unconditional**. `_load_pending`
(`paper_trade_replay.py:1271`) = `SELECT ... FROM paper_trade_record WHERE result IS NULL ORDER BY ts ASC`
(**no division/strategy predicate**) → every pending row of every division is walked; bitunix-symbol rows
fetch paginated 1m history via `_bitunix_kline_fetcher` (`:1152-1225`, ~8 calls/row/tick). This is the
flagging footprint. **Recommended: disable** — `replay_task = None` + guard the cancel/await sites; code
retained, one-line revert.

`set_live_exit_executor(bitunix_observer)` (`main.py:1666`) wires the **futures** observer's dormant Path-C
live-exit fork. SFP does **not** use it. Disabling replay disables this dormant fork (no live impact, since
futures is going inert and SFP is reconciler-driven).

### Part C — Flag + dashboard render (`strategies.yaml`, `web/data.py`, templates)
Config: `bitunix_futures.mode: halted` (new key). When futures is inert:
- **Freeze→None (panels vanish):** score/"recent fires" (`data.py:2561`, q `:2641`, gated `:3333`), HTF
  (`:1775`), PA (`:2245`), decision-flow (`:2396`), pending-PA (`:2153`), trade-plan (`:1923`), HITL counter,
  stage1 trade-flow — all short-circuit to `None` when the observer is unwired/inert.
- **Stays historically populated (reads existing rows):** win-rate `paper_trade_summary` (`:1284`, q `:1315`)
  — shows the historical paper corpus; new rows stop accruing. Not stale-as-bug, but should render a
  **HALTED-INERT badge** so the panel isn't misread as live. Add a `mode` badge in the division template
  analogous to `home.html:146`'s standby badge. (Lowest-risk: badge only; do not delete panels.)

---

## 3. What gets EDITED vs PROVABLY-UNCHANGED (drift-gate targets)

**Edited (this build):**
- `trading_corp/agents/divisions/bitunix_futures_observer.py` — 3 entry guards on `self._halted`.
- `trading_corp/main.py` — read `mode`, pass `halted` to ctor, skip `run_pa_redeem_loop` when halted, disable
  (or gate) `start_replay_loop` at `:1669`/cancel sites.
- `config/strategies.yaml` — `bitunix_futures.mode: halted` (additive key).
- `trading_corp/web/data.py` + division template — HALTED-INERT badge (additive; no panel deletion).

**MUST be byte-identical (md5/AST proof at deploy) — NOT edited:**
- `trading_corp/agents/divisions/bitunix_sfp_observer.py`  ← SFP live signal→place→exit
- `trading_corp/agents/strategies/bitunix_sfp.py`           ← SFP detector
- `trading_corp/agents/divisions/bitunix_position_reconciler.py` ← books SFP live exits
- (No edit needed to) `paper_trade_replay.py` — it's *retained*, just not started. (If you prefer to gate
  inside it instead of at the call site, that flips it to edited — call-site disable keeps it pristine.)

**Tasks that MUST keep running for live SFP (do NOT gate in this build):** `bitunix-bar-cache` 3m
(`main.py:1683`), HTF h1/h4/d1 (`:1702`), htf-funding (`:1714`), capture caches ×6 (`:1735`), bar-archiver
(`:1748`), htf-regime-snapshot (`:1757`), `bitunix-sfp-loop` (`:1782`), reconciler (`:1794+`). These run
unconditionally today; leaving them unconditional satisfies the "bar-cache KEEPS RUNNING" constraint with
**zero** added risk to the live feed. (Gating them on "≥1 trading division" is an optional all-halted-future
nicety with no IP benefit now — recommend **defer**, out of this build's scope.)

---

## 4. ★ THE FORK — replay gate condition (operator decision, blocks build)

**The plan text says:** *"gate the global replay + paper-sim loops on '≥1 bitunix division in mode:trading'."*
**Problem:** SFP is mode:trading → that condition is TRUE → **replay still starts** → it re-walks the existing
`bitunix_futures` pending-paper backlog (no division filter) → **the bitunix kline footprint does NOT drain**
→ the new IP gets re-flagged after the swap. The "≥1 trading division" gate is the right condition for the
**bar-cache family** (keep them while SFP trades), but the **wrong** condition for **replay**.

Replay exists only to classify **paper-sim** rows. The two-state collapse **eliminates the paper-sim state**
(every division is live-trading or inert; none is an active paper sim). So:

**Recommended — DISABLE replay outright** (`replay_task = None`, retained+revertible). Drains the bitunix
historical footprint to ~0 immediately, regardless of backlog. 

**Collateral to confirm before choosing disable (the reason this is a fork, not an auto-call):**
1. Replay is **global**, not bitunix-only. Disabling it also stops paper-sim classification for any **other**
   paper divisions (e.g. Coinbase Donchian / Otter, if running paper). Their *paper analytics* freeze. Only
   the *bitunix-symbol* rows actually fetch from `fapi.bitunix.com` (the flagging traffic); non-bitunix rows
   fetch elsewhere and don't flag the IP — so disabling replay is heavier than strictly needed for the IP, but
   simplest and fully revertible.
2. Confirm **no LIVE non-bitunix division** depends on replay's Path-C live-exit fork. Today
   `set_live_exit_executor` is wired to the **futures** observer only (`main.py:1666`) and is dormant; SFP and
   the reconciler don't use replay. Operator should confirm current live divisions before disabling.

**Options for the operator:**
- **(i) Disable replay** [recommended] — simplest, full bitunix drain, revert = restore the call.
- **(ii) Gate replay on "a paper-sim division exists"** — under two-state that's *never* true → functionally
  identical to (i) but with a gate variable for future re-enable. NOT "≥1 trading division".
- **(iii) Surgical: keep replay, skip only the bitunix-symbol fetches** — preserves other divisions' analytics,
  but more code + more risk; over-engineered for a state we're abolishing anyway.

**Plus, independent of i/ii/iii — the existing backlog:** pending (`result IS NULL`) `bitunix_futures` paper
rows. Once replay is off they're harmless (never fetched). **Optional, separate, operator-gated:** mark them
`result='expired'`/resolved for tidiness. Not required for the drain. (Do **not** auto-do this — it's a data
write to the live ledger.)

---

## 5. Test plan (full suite == baseline; targeted-hunk)
- New unit tests: (a) futures observer with `_halted=True` → `observe_and_decide`/`observe_alert`/redeem all
  return without scoring or `insert_paper_trade_record` (assert no DB write, mock `db.insert_paper_trade_record`);
  (b) `mode:halted` parse → `halted=True`; `mode:trading`/absent → `halted=False` (fail-open to trading? or
  fail-closed to halted? — **recommend fail-OPEN to trading is WRONG here; fail-safe = if unsure, do nothing**,
  i.e. default `trading` only when explicitly set, else treat missing as trading to preserve current behavior —
  confirm with operator); (c) `start_replay_loop` not started when disabled / replay-gate false.
- SFP regression: md5 the 3 untouched files == PROD; run existing SFP test suite (parity + k=1) unchanged.
- Full suite == known baseline (28F per memory) — zero **new** failures.
- Boot smoke: engine constructs futures observer (halted), starts SFP loop + bar-cache + reconciler, does NOT
  start replay, does NOT start futures redeem loop; no ImportError.

## 6. Open verification items / not-touched
- **Missing doc:** the requested `/areas/bitunix-futures.md` does **not** exist in the repo (closest:
  `docs/divisions.md`, `docs/ARCHITECTURE.md`, and the audit `1537de3`). Flag if it lives elsewhere (PKM?).
- Confirm current **live division roster** + that nothing non-bitunix relies on replay (§4 collateral).
- Webhook→`observe_and_decide` path: gate at top of `observe_and_decide` covers all callers; no separate
  webhook edit needed (verify the receiver routes through it, not a private method).
- `mode` default semantics (fail-open vs fail-closed) — operator call (§5b).
- This build does **not** touch the IP swap (plan step b) or TradingView-historical ingest (step c).

## 7. Recommendation
Proceed to build **Part A (futures inert via `mode:halted`)** + **Part B = option (i) disable replay)** +
**Part C (badge)**, leaving bar-cache/HTF/SFP/reconciler untouched and unconditional. This is the minimal,
revertible change that fully drains the bitunix replay footprint while proving SFP byte-unchanged. **Blocked
on the §4 decision (disable vs gate vs surgical) and the live-roster confirmation before I write code.**
