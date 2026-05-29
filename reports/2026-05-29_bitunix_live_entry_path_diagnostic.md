# BitUnix Stage-1 Session N+1 — Phase 1 sub-diagnostic

**Date:** 2026-05-29 · **Type:** read-only structural audit · **Branch:** `bitunix-live-entry-path-2026-05-29` off `main` · **Status:** Phase 1 only — Phase 2 scope decision deferred to operator.

**Premise:** the existing Session N defensive scaffolding (`bitunix-orderpath-safety-2026-05-29`) is dead-but-tested. This audit answers six structural questions the brief calls out, surfaces premise gaps that reshape scope, and recommends a scope tier (A/B/C) for the implementation phase.

---

## TL;DR

- **Two parallel `would_have_placed` wire sites exist** (trigger-path + score-path), not one. They share identical structural blocks (status set + log_proposed_order + log_event + paper_trade_record insert + daily_risk record + decision log). **Recommend a canonical helper** `_record_placement(...)` that both call; the live branch (`execution_mode=="live"`) then forks inside the helper to route to `data_exec.place()` instead of writing the paper record.
- **`execution_mode` does not exist anywhere in the codebase** (`grep` returns no hits). Clean field name. Lands under `bitunix_futures:` in `config/strategies.yaml`, parsed at `main.py:334` (existing `_bx_block` read), passed as new constructor arg `execution_mode: str = "paper"` at `main.py:374-391`.
- **HITL primitive EXISTS and is production-ready.** `comms/pending_registry.py:PendingApprovalRegistry` provides `wait(req, timeout) → BoardDecision` + `resolve(order_id, decision, source)`, fans out to notifiers (telegram message), and is resolved by either `web/routes.py POST /approvals/{order_id}/decide` or telegram inline keyboard. Singleton constructed at `main.py:219`. **No new build needed** — just inject `pending_registry` into the observer.
- **`set_agent_state` + `load_agent_state` already exist** at `persistence/db.py:444,468`. Session N memory's "no existing primitive" assumption was wrong — strategy-state persistence does NOT need a new writer/reader API. **Mechanical scope shrinks**: introduce a `StrategyState.from_persistence(actor, key, db_url)` classmethod that wraps the existing writer/reader; swap 17 construction sites to use it (one-liners).
- **StrategyState construction site count: 17** (drift vs memory's "20+" — 9+4+3+2+1+1). Census below. All construct `halted=False` fresh; persistence load is mechanical.
- **Position-qty translation: NO gap.** `_amount_str` at `brokers/bitunix.py:182` (broker-write branch) does `f"{abs(float(qty)):.8f}".rstrip("0").rstrip(".")` — precision-clean. **Minor Stage-2 cleanup flagged**: no per-symbol min-step-size guard (BitUnix BTC perp ~0.001 step); at Stage-1 sizing (~0.001 BTC) we land above the typical minimum so this isn't a Stage-1 blocker.
- **Alerts surface**: lifecycle/entry events through existing `self.telegram_channel` (per-paper-entry copy variant "(live)" tag); safety/halt events through `data_exec.safety_notifier` (Session N branch, currently `None` on main). **No new channel needed for entry/fill/rejection events.**

**Premise gap that reshapes scope**: `set_agent_state` already exists. Persistence work is smaller than Session N memory predicted (1 helper + 17 one-line swaps + 1 writer at halt-mutation site, not "build writer/reader API + 20+ sites").

---

## 1. Wire-point flow (two parallel sites, not one)

### Site A — trigger-path: `_maybe_propose` 

`trading_corp/agents/divisions/bitunix_futures_observer.py:2361-2447`

Risk eval block at `:2361-2382`:
```python
order = proposal.proposed_order
try:
    account = AccountState(account="bitunix_futures", equity=account_equity, peak_equity=account_equity)
    strat_state = StrategyState(strategy="bitunix_futures")
    verdict_risk = self.risk_agent.evaluate(order, account, strat_state, None, None)
except Exception as e:
    self._log_decision(verdict, original_payload, "error_risk_eval", ...)
    return
if verdict_risk.verdict == "reject":
    order.status = "risk_rejected"
    ...
    return
if verdict_risk.verdict == "resize" and verdict_risk.new_qty is not None:
    order.qty = float(verdict_risk.new_qty)
```

Wire site at `:2384-2425`:
```python
# ── Place (paper-mode auto-execute via data_exec) ────────────
order.status = "would_have_placed"                                 # :2388  ← WIRE POINT
self.logger_agent.log_proposed_order(order)                        # :2389
self.logger_agent.log_event(
    actor="bitunix_futures", kind="would_have_placed",
    payload={...},
)                                                                  # :2390-2409
try:
    record = PaperTradeRecord.from_order(order, ...)
    db.insert_paper_trade_record(record.to_db_row(), db_url=self.db_url)  # :2422
except Exception as e:
    log.warning(...)

self._record_daily_risk(utc_date, proposal.effective_risk_pct or 0.0)  # :2426
self._log_decision(verdict, original_payload, "placed", ...)            # :2427
```

Followed by telegram push at `:2441-2447` (separate from the placement block).

### Site B — score-path: `_score_and_maybe_propose_locked`

Risk eval block at `:1454-1473` — structurally identical to Site A but with `_log_score_decision` instead of `_log_decision`.

Wire site at `:1475-1555`:
```python
# ── paper-mode placement ──
order.status = "would_have_placed"                                 # :1476  ← WIRE POINT
order.rationale = f"[score] {order.rationale}"                     # :1479
order.extra["score_path"] = True                                   # :1480
order.extra["net_score"] = verdict_score.breakdown.net_score       # :1481
if htf_funding_rate_at_decision is not None:
    order.extra["funding_rate_at_decision"] = htf_funding_rate_at_decision
if redeem_metadata is not None:
    order.extra.update({k: v for k, v in redeem_metadata.items() if k != "audit_row_id"})
self.logger_agent.log_proposed_order(order)                        # :1497
self.logger_agent.log_event(
    actor="bitunix_futures", kind="would_have_placed",
    payload={..., "via": "bitunix_score", ..., "redeemed": bool(redeem_metadata), ...},
)                                                                  # :1498-1525
try:
    record = PaperTradeRecord.from_order(order, ...)
    record.extra = dict(order.extra)
    db.insert_paper_trade_record(record.to_db_row(), db_url=self.db_url)  # :1538
except Exception as e:
    log.warning(...)

if redeem_metadata is not None and redeem_metadata.get("audit_row_id"):
    self._backfill_redeem_order_id(int(redeem_metadata["audit_row_id"]), order.id)  # :1547-1550

self._record_daily_risk(utc_date, proposal.effective_risk_pct or 0.0)  # :1552
self._record_score_fire(order.side, now.isoformat(), verdict_score.tier.value)  # :1553
self._log_score_decision(payload, verdict_score, "placed", ...)         # :1554
```

Followed by score-path telegram at `:1557-1573`.

### Site comparison

| Element | Site A (trigger) | Site B (score) |
|---|---|---|
| `order.status = "would_have_placed"` | yes :2388 | yes :1476 |
| `log_proposed_order(order)` | yes :2389 | yes :1497 |
| `log_event(kind="would_have_placed", ...)` | yes :2390-2409 | yes :1498-1525 (adds `via=bitunix_score`, `net_score`, `redeemed`, `bars_waited`) |
| `insert_paper_trade_record` | yes :2422 | yes :1538 (with `record.extra = dict(order.extra)` passthrough) |
| `_record_daily_risk` | yes :2426 | yes :1552 |
| `_record_score_fire` | no | yes :1553 |
| `_log_*_decision("placed")` | `_log_decision` :2427 | `_log_score_decision` :1554 |
| Followed by telegram | yes :2441-2447 | yes :1557-1573 |

### Recommendation: canonical helper

**Single wire point via `_record_placement_outcome(order, extra_audit_payload, decision_logger, ...)`** — a private helper that handles the 6 shared steps (status set + log_proposed_order + log_event + paper_trade_record insert + daily_risk + decision log). The two sites differ in:

1. **Audit payload extras** (`via`, `net_score`, `redeemed`, `bars_waited` for score-path) → pass as `extra_audit_payload: dict` parameter.
2. **`record.extra` passthrough** (score-path only) → helper unconditionally copies `dict(order.extra)` (safe for trigger-path since trigger writes a minimal `order.extra`).
3. **Score-only `_record_score_fire`** → callable parameter `post_record_callbacks: list[Callable]` (or just call after `_record_placement_outcome` returns).
4. **Decision-log function** (`_log_decision` vs `_log_score_decision`) → pass the callable.
5. **Redeem-metadata backfill** (score-path only) → call before/after `_record_placement_outcome`.

The live branch lives **inside** `_record_placement_outcome`:

```python
async def _record_placement_outcome(self, order, proposal, audit_payload_extra, ...):
    if self.execution_mode == "live" and self._live_eligible(order):
        # HITL gate (first N): await self._pending_registry.wait(...)
        # then: fill = await self.data_exec.place(order, division="bitunix_futures")
        # write live audit (kind="live_order_placed") + paper_trade_record mirror (live-tagged)
        ...
    else:
        # existing paper path: status="would_have_placed" + log + record
        ...
```

This keeps both call sites at one line each (`await self._record_placement_outcome(...)`) and concentrates the live/paper fork in one place.

---

## 2. `execution_mode` config landing

### Active YAML block

`config/strategies.yaml:1019-1038`:
```yaml
bitunix_futures:
  enabled: true                     # Phase 3.1 paper-mode auto-execute live
  auto_execute: true                # NO per-trade HITL — caps are the gate
  division: bitunix_futures
  symbol: "BTC/USDT.P"
  counter_tier_enabled: false
  effective_risk_per_trade_pct: 0.005
  daily_risk_kill_pct: 0.03
  min_rr_ratio: 1.5
  default_tp_r_multiple: 2.0
  stop_floor_pct: 0.003
  bias_decay:
    timeframe_4h_seconds: 86400
    timeframe_1d_seconds: 604800
  cvd_decay_seconds: 1800
  tier_sizing:
    PREMIUM:  {size_pct: 0.04, leverage: 8.0}
    STANDARD: {size_pct: 0.02, leverage: 5.0}
    WEAK:     {size_pct: 0.01, leverage: 2.0}
    COUNTER:  {size_pct: 0.005, leverage: 2.0}
```

`execution_mode` is **absent throughout the codebase** (grep verified).

### Observer config landing

Observer constructor at `trading_corp/agents/divisions/bitunix_futures_observer.py:421-446` takes structured config objects (`BitUnixConfluenceConfig`, `PAValidationConfig`, `HTFRegimeConfig`, `StrategyConfig`, `FeeConfig`); the observer itself **does not read YAML directly**.

The YAML→constructor parse happens at `trading_corp/main.py:325-356`:
```python
_strat_path = _Path(__file__).resolve().parent.parent / "config" / "strategies.yaml"
with _strat_path.open() as _f:
    _strat_raw = _yaml.safe_load(_f)
_bx_block = _strat_raw.get("bitunix_futures", {}) or {}
_scoring_config = BitUnixConfluenceConfig.from_dict(_bx_block)
_pa_config = PAValidationConfig.from_dict(_bx_block)
_htf_gate_mode = str((_bx_block.get("htf_gate") or {}).get("mode", "off")).lower()
...
```

New parse line at the same point:
```python
_execution_mode = str(_bx_block.get("execution_mode", "paper")).lower()
if _execution_mode not in ("paper", "live"):
    log.warning("bitunix execution_mode %r unknown — defaulting to paper", _execution_mode)
    _execution_mode = "paper"
```

Observer construction at `main.py:374-391` adds the kwarg:
```python
bitunix_observer = BitunixFuturesObserver(
    db_url=secrets.db_url,
    ...
    execution_mode=_execution_mode,
    pending_registry=pending_registry,  # for HITL gate
    data_exec=data_exec,                # already wired
)
```

### Recommendation: semantics

- **Field name**: `execution_mode: "paper" | "live"`. Default `"paper"`. Unknown values fall back to `"paper"` with a warning.
- **Per-strategy** (under each strategy block in `strategies.yaml`), not global.
- **Config-and-restart, NOT runtime toggle.** Live requires explicit YAML edit + process restart. No mtime hot-reload of `execution_mode` — too sharp an edge (a stray file write could flip live).
- **Conjunction with `auto_execute`**: `execution_mode == "live" AND auto_execute == True` BOTH required to place a real order. `auto_execute: false` becomes an emergency soft-disable (operator can flip it in YAML without changing execution_mode, and the existing mtime cache picks it up). **Caveat**: today the bitunix observer does NOT read `auto_execute` at all — see §4.
- **Process-mode interaction**: `--paper` process flag still overrides everything (CLAUDE.md invariant 3). A `--paper` process with `execution_mode: live` cannot place real orders because `_build_broker_for_division` wraps in `PaperExecutionBroker`. Live placement requires both `--live` AND `execution_mode: live` AND `auto_execute: true`.

---

## 3. HITL primitive status: **EXISTS, no build needed**

`trading_corp/comms/pending_registry.py:PendingApprovalRegistry` is the canonical primitive. Production-ready as of 2026-05-03 (Phase B.1 of HITL-in-app direction per CLAUDE.md §HITL surface direction).

### Surface

- **`async wait(req: ApprovalRequest, timeout_s: float = 3600.0) → BoardDecision`** (`:86-152`) — orchestrator-side: register Future, fan out notifiers (telegram message etc.), block until resolved or timeout. Returns `BoardDecision(decision="reject", reason="approval timeout")` synthetic reject on timeout.
- **`resolve(order_id, decision, source, also_resolve_paired=False) → bool`** (`:156-223`) — resolver-side: called from web POST `/approvals/{order_id}/decide` (source='web') or telegram callback (source='telegram'). First-call-wins; second returns False for 409.
- **Audit chain**: `pending_approval_added` → `board_decision_received` (tagged with source) — already wired.
- **Singleton** constructed at `main.py:219` (`pending_registry = PendingApprovalRegistry(logger_agent=logger_agent)`); passed into `WebDeps` + `TelegramChannel`.

### Wire pattern for bitunix observer

The observer takes a new constructor arg `pending_registry: PendingApprovalRegistry | None = None`. In the live branch of `_record_placement_outcome`:

```python
if self._needs_hitl(order):  # first N live orders, count via agent_state
    req = ApprovalRequest(
        order_id=order.id,
        summary=self._build_approval_summary(order, proposal, verdict),
        detail=order.to_db_row() | {"extra": order.extra, "verdict": verdict_dict, "tier": tier},
    )
    decision = await self._pending_registry.wait(req, timeout_s=600)
    if decision.decision != "approve":
        # audit + don't place
        self.logger_agent.log_event(actor="bitunix_futures", kind="live_order_skipped_hitl",
            payload={"order_id": order.id, "decision": decision.decision, "reason": decision.reason})
        return
    if decision.new_qty is not None:
        order.qty = float(decision.new_qty)

fill = await self.data_exec.place(order, division="bitunix_futures")
```

### HITL counter mechanism (first-N)

Use existing `set_agent_state("bitunix_futures", "live_orders_placed", N)` + `load_agent_state(...)`. The `_needs_hitl(order)` method reads the counter; once `N >= self.hitl_first_n_threshold`, skip the gate. **Persist across restarts** — counter must survive restart for the "first N" semantic to mean what the operator expects.

Recommended threshold: `hitl_first_n_threshold=10` (configurable). Easy to widen to 20 or shrink to 5 with a YAML edit.

---

## 4. `auto_execute` semantics today

**Observer does NOT read `auto_execute` at all** today (`grep auto_execute trading_corp/agents/divisions/bitunix_futures_observer.py` returns 0 matches in functional code — only a comment at line 20 explaining the YAML setting).

The current behavior matches Site A's inline comment at `:2385-2387`:
> No HITL approval gate per board direction (memory `trading_corp_bitunix_phase3_confluence_model`). Risk caps are the gate, not per-trade approval.

So `auto_execute: true` in YAML is informational — the observer always proceeds to `would_have_placed`. **Recommendation**: in the new live branch, read both `execution_mode` and `auto_execute` fresh from YAML on every order decision (via a per-decision YAML re-read with mtime cache — matches the `graph/ceo_graph.py:_check_auto_execute` pattern). This gives the operator a fast kill switch via YAML edit without restart.

- `execution_mode: live` + `auto_execute: true` → place via `data_exec.place()` (with HITL gate for first N).
- `execution_mode: live` + `auto_execute: false` → fall back to `would_have_placed` audit (paper-record path). Operator soft-disabled live without changing execution_mode.
- `execution_mode: paper` → unchanged `would_have_placed` path regardless of auto_execute.

The dual flag is intentional: `execution_mode` is the structural gate (config-and-restart), `auto_execute` is the runtime kill switch (mtime-hot-reload). Both required is fail-closed.

---

## 5. StrategyState construction site census

Per Session N memory: "9 in main.py + 4 webhooks + 3 routes + 2 observer + 1 telegram_commands + 1 graph" = 20. **Actual count: 17.**

| File | Sites | Lines |
|---|---|---|
| `trading_corp/main.py` | 8 | 2363, 2534, 2685, 2847, 3000, 3155, 3450, 3624 |
| `trading_corp/main.py` (`_ICStrategyState`) | 2 | 1158, 1235 (different type — IC-specific subclass, excluded from count) |
| `trading_corp/web/webhooks.py` | 2 | 599, 861 |
| `trading_corp/web/routes.py` | 3 | 930, 1124, 1385 |
| `trading_corp/comms/telegram_commands.py` | 1 | 470 |
| `trading_corp/graph/ceo_graph.py` | 1 | 337 |
| `trading_corp/agents/divisions/bitunix_futures_observer.py` | 2 | 1459, 2365 |
| **Total** | **17** | |

Every site constructs `StrategyState(strategy=<name>, halted=False)` fresh. None consult persistence today.

### `StrategyState` shape

`trading_corp/persistence/models.py:128-135`:
```python
@dataclass
class StrategyState:
    strategy: str
    halted: bool = False
    halt_reason: str | None = None
    realized_pnl: float = 0.0
    realized_pnl_day: str | None = None
    updated_ts: str = field(default_factory=...)
```

### Persistence primitives **already exist** (premise gap)

`trading_corp/persistence/db.py:444-491`:
- `set_agent_state(agent, key, value, db_url) → None` — JSON upsert with `updated_ts`.
- `load_agent_state(agent, key, db_url) → (value, updated_at) | None` — JSON read.

Session N memory said "Confirm no existing primitive does this" → **IT EXISTS**. Persistence scope shrinks accordingly.

### Recommended load helper

```python
# in persistence/models.py
@classmethod
def from_persistence(
    cls, strategy: str, db_url: str = "sqlite:///data/trading_corp.db",
) -> "StrategyState":
    """Construct with halted/halt_reason loaded from agent_state. Other
    fields stay at default (they're transient per-call computations)."""
    from trading_corp.persistence.db import load_agent_state
    loaded = load_agent_state("strategy_state", strategy, db_url=db_url)
    if loaded is None:
        return cls(strategy=strategy)
    value, _ = loaded
    return cls(
        strategy=strategy,
        halted=bool(value.get("halted", False)),
        halt_reason=value.get("halt_reason"),
    )
```

Writer side: wherever `StrategyState.halted` is mutated (today only `RiskAgent.evaluate()` returning a halted state; verify), call `set_agent_state("strategy_state", strategy, {"halted": True, "halt_reason": ...})`. **Single site** if mutation is centralized; otherwise grep for `\.halted *=` to find all writers.

### Mechanical scope

- 1 helper method (`StrategyState.from_persistence`) + 1 writer site (or a small number, gated by grep)
- 17 construction-site swaps: `StrategyState(strategy=X)` → `StrategyState.from_persistence(X, db_url)`
- 1 test asserting halt persists across observer re-instantiation
- All ~30 LOC of mechanical edits

**Conclusion**: persistence shrinks from "design + build writer/reader API + 20+ sites" to "1 helper + 17 one-liners + 1 writer". Estimated <1 hour mechanical work + tests.

---

## 6. Position-size translation: NO gap, minor Stage-2 flag

### Float → string boundary

Broker-write branch `trading_corp/brokers/bitunix.py:182-186`:
```python
def _amount_str(qty: float) -> str:
    s = f"{abs(float(qty)):.8f}".rstrip("0").rstrip(".")
    return s or "0"
```

Examples:
- `0.001` → `"0.001"`
- `1.0` → `"1"`
- `0.00099999` → `"0.00099999"` (truncated, NOT rounded)
- `1e-9` → `"0"` (rstrip removes trailing zeros, then trailing dot, leaving empty → `"0"`)

Observer `qty = notional / entry_price` (`bitunix_futures_observer.py:~2118` for trigger-path, `~1934` for score-path): both compute as plain Python float. No intermediate string conversion. The `_amount_str` formatter is the only place precision could be lost; at Stage-1 sizes (~0.001 BTC), the result is precision-stable.

### Edge case: no min-step-size guard

BitUnix BTC perp has a typical step size of 0.001 (1e-3). `_amount_str(0.001)` → `"0.001"` ✓. But for hypothetical post-resize edge cases:
- `0.00099999` → `"0.00099999"` → broker rejects (below step).
- `0.0009` → `"0.0009"` → broker rejects.

This could surface if `RiskAgent` resizes a `0.001` order to `0.0009` (mathematical sizing produces a sub-step amount). Not a Stage-1 blocker because Stage-1 risk caps keep all orders well above 0.001 BTC; but **flag as Stage-2 cleanup**: add a `_round_qty_to_step(qty, symbol)` step inside the live placement path.

**Verdict**: NO Stage-1 blocker at the float→string boundary. Session N's "no gap" conclusion stands.

---

## 7. Operational alerts surface

### Existing channels

- **`self.telegram_channel`** on observer — currently sends paper-entry messages ("BTC-PERP TIER LONG (paper)") at trigger-path `:2441-2447` and score-path `:1557-1573`. Wired at `main.py:791`.
- **`data_exec.safety_notifier`** — introduced on Session N safety branch (NOT on main). Currently `None`-defaulted; main.py wiring deferred to N+1. Used by `data_exec.place()` for `BitunixPositionModeMismatch` and by `data_exec.flatten_division()` for halt/flatten events.
- **`bitunix_lifecycle_notifier.py`** — separate telegram path for TP/SL/close-out resolution events (write-once on resolved trade).

### Routing for live-order events

| Event | Channel | Audit kind |
|---|---|---|
| `live_order_placed` (entry constructed, awaiting fill) | `self.telegram_channel` (existing paper channel, copy "(live)") | `live_order_placed` (new audit kind) |
| `live_order_filled` (fill received from broker) | `self.telegram_channel` | `filled` (reuse data_exec's existing audit kind at `data_exec.py:155-168`) |
| `live_order_rejected` (broker raised pre-fill) | `self.telegram_channel` | `live_order_rejected` (new) |
| `live_order_skipped_hitl` (HITL gate rejected) | `self.telegram_channel` | `live_order_skipped_hitl` (new) |
| `mode_mismatch_detected` / kill-switch fired | `data_exec.safety_notifier` (Session N safety branch, deferred wiring) | existing |
| `flatten_account_failed` | `data_exec.safety_notifier` | existing |
| TP/SL/close-out (existing) | `bitunix_lifecycle_notifier.py` | existing |

**Recommendation**: route entry/fill/rejection/HITL-skip through `self.telegram_channel` (matches paper pattern; operator sees one feed for "trade activity"). Route halt/safety/kill-switch through `data_exec.safety_notifier` (matches Session N pattern; operator sees a separate feed for "operator-must-react"). **No new channel needed** for entry events.

The safety_notifier wiring (`main.py: data_exec.safety_notifier = channel` or a separate channel) is a Session N+1 polish item — without it the safety branch's mode-mismatch handler writes the audit but the telegram side-effect no-ops. Recommend wiring in this session alongside the live entry-path work.

---

## Phase 2 scope recommendation: **(B) Narrowed scope**

### Scope A — full N+1 (execution_mode + wire + HITL + persistence)
**Feasible.** HITL primitive exists; persistence primitive exists; observer wire-point consolidation is mechanical. Estimated 1-2 focused sessions.

### Scope B — narrowed (execution_mode + wire + persistence; HITL deferred)
**Not recommended.** HITL primitive is already production-ready; deferring it adds session count without removing build effort. The HITL counter (first-N gate) is the only new piece, and `set_agent_state` is already in place.

### Scope C — deeper refactor surfaced
**Not applicable.** The two parallel sites can route through one canonical helper cleanly (per §1). No structural blocker found.

### Recommended: **Scope A — full N+1**

**Rationale**: every primitive needed already exists. The session work is composition + wiring + tests, not new infrastructure. Splitting reduces parallelism and risks the persistence half losing the safety side (HITL gate is the load-bearing protection for first live orders — deferring it weakens the "live is safe" claim).

**Estimated breakdown** for the implementation phase (Phase 3):
1. **Canonical wire helper** `_record_placement_outcome` extracted from both sites (~80 LOC, 4 tests). Refactor-only commit; no behavior change at first.
2. **`execution_mode` YAML field + constructor + paper-default tests** (~40 LOC, 2 tests).
3. **Live branch inside helper** — `data_exec.place()` call + audit `live_order_placed` + telegram (~100 LOC, 3 tests with mocked data_exec).
4. **HITL gate** — registry.wait + first-N counter via agent_state (~60 LOC, 4 tests covering approve/reject/timeout/counter increment).
5. **Strategy-state persistence** — `from_persistence` classmethod + 17 site swaps + writer at halt-mutation site + persistence test (~50 LOC of edits + helper).
6. **safety_notifier wiring** in main.py (~5 LOC).
7. **Memory + BACKLOG update + merge sequencing plan** in close-out.

Each commit is its own scope per the tight-commit discipline.

---

## Premise gaps surfaced

1. **`set_agent_state`/`load_agent_state` already exist** — Session N memory predicted "no existing primitive" → wrong. Persistence scope shrinks ~3x.
2. **Two parallel wire sites, not one** — Session N memory said "the score-path equivalent, :1557-1573" but those lines are the post-place telegram, not the wire point. Actual score-path wire is `:1475-1555`. Canonical helper still tractable.
3. **HITL primitive complete** — `PendingApprovalRegistry` with web + telegram resolvers, audit chain, paired-resolve support, timeout handling. No build needed; only wire-in.
4. **StrategyState count: 17, not 20** — minor drift; doesn't reshape work.
5. **Observer does NOT read `auto_execute` today** — informational only. Recommended fresh-read-on-decision pattern matches graph path.
6. **safety_notifier wiring is the natural session-N+1 cleanup** — Session N left main.py wiring deferred to "when trigger actually goes live"; this is that session.

---

## Cross-branch / merge sequencing flag

The brief notes: the broker-write branch (`72f0eb6`) defines `BitunixPositionModeMismatch` inline; the safety branch (`5fbf762`) imports it from `bitunix_exceptions.py`. Class-identity reconciliation required before either merges. **Recommendation** for merge sequencing (carry into close-out):

1. **C-1 branches first** (independent of bitunix code): `c1-bitunix-cred-rotation`, `c1-apify-cred-rotation`, `c1-tastytrade-verify-2026-05-29` — these are KV-side rotation work that landed on origin but should be reviewed/merged independently.
2. **Exception-class move on broker-write** — single follow-up commit on `bitunix-live-engine-stage1-broker-write` that imports `BitunixPositionModeMismatch` from `bitunix_exceptions.py` instead of defining inline. ~5 LOC.
3. **Broker-write → main** — adds `place_order` (still dead code on prod until observer wires to it).
4. **Safety branch → main** — adds defensive consumers + `flatten_division`; still dead code without execution_mode flip.
5. **This branch (live entry-path) → main** — wires the consumer; still dead code until `execution_mode: live` flip in YAML.
6. **Subsequent live deploy** — separate operator decision; not in this session's scope.

---

## Hard constraints carried forward

- **No live orders flow this session.** Default YAML stays `execution_mode: paper` (or unset, with default).
- **No restart** (no deploy).
- **Secrets never touch the session** — no rotation work in this branch.
- **Branch unmerged**; no merging without operator sign-off on the cross-branch exception-class identity flag.
- **Tighter commits** — Phase 1 diagnostic (this report) is its own commit; Phase 3 implementation is per-piece commits.

---

*Sources: read-only code audit 2026-05-29 (file:line cited inline); memories `bitunix-order-path-safety-pattern`, `bitunix-live-engine-build`, `session-2026-05-29-marathon-eos`, `telegram-audit-success-is-confirmed-delivery`; planning doc `runbooks/2026-05-29_bitunix_live_readiness_audit.md`; pre-existing `comms/pending_registry.py`, `persistence/db.py`, `graph/interrupts.py`. No code/config/deploy changes made.*
