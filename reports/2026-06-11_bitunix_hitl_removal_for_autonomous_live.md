# Bitunix HITL Removal for Autonomous Live — Surface, Clean Path, Breaker Coverage

**Date:** 2026-06-11 · **Session:** operator-supervised, **analysis-only**, read-only agent SSH (policy `82fda13`)
**Branch:** `bitunix-hitl-removal-analysis-2026-06-11` (off `origin/main` `b1e4150`, with B1 deployed; unmerged)
**Question:** make Bitunix trade autonomously (no per-order human approval). Map the HITL surface, the clean removal, and verify the circuit breakers that become the *only* safety layer once approval is gone.

> ## VERDICT — **Removal is mechanically CLEAN (one constant). The breakers are NOT sufficient as-is. Do NOT remove HITL until the items below are fixed.**
> **🔴 BLOCKER — the account drawdown auto-flatten (15%) is a PLACEHOLDER that NEVER fires.** Both observer
> risk-eval call sites pass `peak_equity = current equity` (`bitunix_futures_observer.py:1518, :3247`), so
> `drawdown_pct()` is **always 0** (`models.py:352-355`) and the `flatten_account` verdict is never produced
> (`risk.py:175-182`). `main.py:2516-2518` documents this as a not-yet-built Phase-3 item. **The kill switch's
> only autonomous trigger rides this dead path, and there is no operator-facing manual kill surface**
> (`data_exec.py:420` calls it a *"future operator surface"*). So once HITL is gone, **nothing automatically
> flattens the account on a drawdown, and nothing auto-invokes the kill switch.**
> **🟠 The per-order backstop (B1 server-side stop) is UNVERIFIED on a real fill** (live test aborted pre-fire).
> It is the thing that bounds a single bad order's loss in place of the human — relying on it unproven is unsafe.
> **Recommendation: harden the drawdown-flatten + kill path, and validate B1 on a real fill, BEFORE removing HITL.**

---

## 0. Scope, hard stops, disclosure
- **Analysis only.** No change to HITL/risk/breakers; no prod write. Removal is operator-gated + §4-relevant.
- **Hard stops (status):** any change → STOP (**not triggered**); prod write → STOP (**not triggered**); **a circuit breaker found placeholder/disabled → flag BLOCKER, surface unmissably (TRIGGERED — drawdown-flatten, §3/§4).**
- **Disclosure (`82fda13`):** local source reads + two read-only sub-agents (HITL surface map, breaker verification); no prod contact this session beyond the read-only state already captured. No writes.
- **Out of scope:** implementing the removal; the live test; B2/maker; Polymarket; reversal-whipsaw + approval-latency.

## 1. HITL surface (file:line — map it all so removal is complete, not partial)

| file:line | role |
|---|---|
| `bitunix_futures_observer.py:234` | `HITL_FIRST_N_LIVE_ORDERS = 10` — **code constant** (no YAML/env), the gate threshold |
| `…:237` | `HITL_WAIT_TIMEOUT_SECONDS = 600.0` — wait timeout → synthetic reject |
| `…:241` | `LIVE_ORDERS_PLACED_AGENT_STATE_KEY = "live_orders_placed"` — persistent counter key |
| `…:533` | `self.pending_registry = pending_registry` (ctor) |
| `…:2359-2402` | `_live_orders_placed_count()` / `_increment_live_orders_placed_count()` — cross-restart counter |
| `…:2630` `_place_live` | the live-placement method containing the gate |
| `…:2701-2706` | `current_count`; `is_monitor_mode = current_count >= HITL_FIRST_N_LIVE_ORDERS`; `needs_hitl = not is_monitor_mode and pending_registry is not None` |
| `…:2715-2717` | `hitl_gate` stamp in the `live_order_placed` audit (`required`/`monitor_mode`/`skipped_no_registry`) |
| `…:2752-2808` | the `if needs_hitl:` block — builds `ApprovalRequest`, `await pending_registry.wait(req, timeout)`, reject/modify/error branches |
| `…:2811-2812` | `data_exec.place(order, "bitunix_futures")` — broker call (reached after approve OR in monitor-mode) |
| `…:2890` | telegram suffix `(live, monitor-mode)` vs `(live)` |
| `comms/pending_registry.py:55,86-152,156-223` | `PendingApprovalRegistry`: `wait()` (blocks on Future, timeout→synthetic reject), `resolve()` (sets result; called by web+telegram) |
| `main.py:219` | `pending_registry = PendingApprovalRegistry(...)` — single instance |
| `main.py:399-404, 647` | passed to `BitunixFuturesObserver(...)` and `WebDeps(...)` |
| `web/routes.py:1527-1554, 1832-1907` | `/approvals` index + `POST /approvals/{order_id}/decide` → `registry.resolve(...)` |
| `web/data.py:1518-1593` | `hitl_activity_24h` dashboard tile; counts `live_order_placed` with `hitl_gate='monitor_mode'` as "autonomous live", `severity='red'` if >0 |

## 2. Clean removal — minimal, complete, no partial gate

**Change exactly one line:** `bitunix_futures_observer.py:234` → `HITL_FIRST_N_LIVE_ORDERS = 0`.

Trace: `is_monitor_mode = current_count >= 0` is **always True** (the counter is ≥0 and only increments) → `needs_hitl` is **always False** → the `if needs_hitl:` block is never entered → the order flows straight to `data_exec.place()` via the **already-exercised monitor-mode path**. It is a **code constant**, not config — so this is a §4 code change, not a YAML toggle.

**Nothing downstream depends on the approval having run:** `log_proposed_order` (`:2744`) and `_record_daily_risk` (`:2750`) run *before* the gate; `data_exec.place` (`:2812`), the `live_order_placed`/`filled` audit, the Path-C `paper_trade_record` write, and `_increment_live_orders_placed_count` (`:2855`) all execute identically. **One cosmetic side-effect:** every order is stamped `hitl_gate='monitor_mode'`, so the `hitl_activity_24h` dashboard tile shows `severity='red'` and counts all live orders as "autonomous live" (`data.py:1573-1583`) — display only, no flow impact. The `PendingApprovalRegistry` can stay wired (dormant) or be removed later; leaving it is harmless. **Removal is clean and complete with the single constant.**

## 3. Circuit-breaker inventory — VERIFIED (real vs placeholder, fires-without-human)

| # | Breaker | Status | Fires w/o human? | Threshold | Trigger |
|---|---|---|---|---|---|
| 1 | Pre-trade risk gate `RiskAgent.evaluate` | **REAL** | **Yes** | per-trade 1.5%, daily-loss 3%, (drawdown 15% → see #2) | order-triggered, fail-closed (`observer:3253-3257` except→`return`) |
| 2 | **Account drawdown auto-flatten** | **🔴 PLACEHOLDER — NEVER FIRES** | **No** | 15% — *unreachable* | `peak_equity=equity` always → `drawdown_pct()=0` (`models.py:352-355`, `observer:1518/3247`, documented `main.py:2516-2518`) |
| 3 | Kill switch `BitunixBroker.flatten` | **mechanism REAL; not autonomously reachable** | **No** (auto trigger = the dead #2; **no manual surface** — `data_exec.py:420` "future operator surface") | — | halt+cancel_all+close_all (`bitunix.py:1339`) |
| 4 | Snapshot-staleness halt | **REAL** | **Yes** | 60s, fail-closed | continuous (every placement) — `_assert_snapshot_fresh` |
| 5 | Daily-risk-kill `DAILY_RISK_KILL_PCT` | **REAL** | **Yes** | 3% cumulative at-risk/UTC-day (`observer:197`) | order-triggered (`skipped_daily_kill`) |
| 5b | `per_strategy_daily_loss_pct` (RiskAgent) | **REAL** | **Yes** | 3% realized loss/day, persisted halt (`risk.yaml`) | order-triggered |
| 6 | **B1 server-side stop** (`slPrice`/`slStopType=MARK_PRICE`/`slOrderType=MARKET`) | **REAL in code; 🟠 UNVERIFIED on a live fill** | **Yes** (venue-side, once placed) | structural stop from `order.extra["stop_price"]` | per-order, venue-autonomous |

Also noted: `correlation_cap: 0.7` in `risk.yaml` is a **documented unenforced placeholder** (moot today — bitunix is single-symbol BTC). A **portfolio-level `catastrophic_stop` (-10% session)** exists (`main.py:1159-1166`, `telegram_batcher`) but is tied to the options/portfolio path; **its coverage of bitunix futures is unconfirmed** — do not assume it backstops bitunix.

**Defects:**
- **D1 (BLOCKER): drawdown-flatten never fires** — `peak_equity` is not tracked (set to current each call). The 15% account cap is dead.
- **D2: the score path doesn't even dispatch the flatten** — `_maybe_flatten_on_risk_verdict` is only called on the Phase-3.1 path (`observer:3265`), not the score path (`~:1528`). Even if D1 were fixed, a drawdown breach arriving via the score path wouldn't flatten.
- **D3: no autonomous or fast-manual kill** — the only auto-trigger is the dead #2; no dashboard/telegram flatten button exists (it's a "future operator surface"). Manual kill = the `bitunix_panic_halt.md` runbook (SSH).
- **D4: no max-orders-per-day / rate cap** — only the cumulative 3% at-risk ceiling bounds fire-rate; many small-risk orders could place until that bucket fills.

## 4. The safety GAP exposed by removal

Per-order human approval is today the catch-all that a person reviews every live order. Remove it and the autonomous net is:
- **Per order:** risk gate (sizes/blocks, fail-closed) ✓ + B1 stop (bounds the single-trade loss at the venue) — **but B1 is unverified on a real fill (🟠)**. If B1 silently fails to attach on a real order, that order is a **naked** leveraged position with no per-order backstop.
- **Cumulative:** daily-risk-kill 3% + daily-loss 3% ✓ (order-triggered, real). No order-count cap (D4).
- **Account-level:** **NONE that works.** The 15% drawdown auto-flatten is dead (D1/D2); the kill switch has no autonomous trigger and no operator button (D3). There is **no continuous account monitor** — all risk evaluation is order-triggered.

**Net:** removing HITL is safe *only if* every order is reliably bounded by B1 and the account has a working drawdown/kill backstop. Today **neither condition holds**: B1 is unproven on a real fill, and the account-level auto-flatten is a placeholder. So **autonomous live as-is would run with no automated account-level stop and an unverified per-order stop** — the human was silently covering both.

## 5. Residual human touchpoints (beyond per-order HITL)
Removing the per-order gate does **not** by itself yield systemd-autonomous live:
- **`--live` interactive confirmation** (`main.py:153-162` `confirm_live` reads stdin for the typed word `LIVE`) — **systemd-incompatible** (no stdin → EOF → exit 2). Autonomous live under systemd requires a **non-interactive `--live` path** (a code change: env/flag bypass) — otherwise live only runs as a hand-launched foreground process (not autonomous). This is the bigger autonomy blocker than HITL.
- **`auto_execute`** (`bitunix_futures.auto_execute`, hot-reloaded) — a kill-switch flag, not a per-order gate; set once. Not a human-in-loop per order.
- **Manual start / flip** — someone sets `execution_mode: live`, `--live --brokers bitunix`, and starts the process.

## 6. Recommendation

**Removal mechanics: clean** (one constant, `HITL_FIRST_N_LIVE_ORDERS=0`; nothing downstream breaks). **Breaker sufficiency: NO — harden first.** Ordered prerequisites before autonomous live:
1. **Fix D1 (BLOCKER):** track a real account high-water-mark `peak_equity` (persist it; e.g. in `StrategyState`/`agent_state`) so `drawdown_pct()` is real and the 15% `flatten_account` verdict can fire. **Fix D2:** dispatch `_maybe_flatten_on_risk_verdict` on the score path too. Without these there is no working account-level auto-protection.
2. **Validate B1 on a real fill** (the deferred Phase-6): with HITL gone, B1 is the per-order backstop replacing the human — it must be confirmed to rest server-side on a real order before it's relied on autonomously.
3. **Build a real kill path (D3):** a continuous account-equity monitor that can auto-invoke `flatten_division`, plus a fast operator surface (dashboard/telegram button), since the current autonomous trigger is dead and manual is SSH-only.
4. **Solve the `--live` autonomy blocker (§5):** a non-interactive live-confirm path so the supervised systemd service can run live — otherwise "autonomous" still requires a hand-launched foreground process.
5. **Consider D4:** a max-orders-per-day / rate cap as defense-in-depth.

If the operator wants autonomy *now*, the minimum safe set is **1 + 2** (working account drawdown-flatten + verified B1); 3–5 are strongly recommended hardening. Removing HITL before #1 and #2 is **not safe** — it removes the human while the replacement safety net has a dead breaker and an unproven stop.

## 7. §4-validation note (for the eventual removal)
The removal is a **strategy-execution change** (CLAUDE.md §4 gate). Before shipping `HITL_FIRST_N_LIVE_ORDERS=0`: (a) land + test the peak-equity/drawdown fix (#1) with a unit test that a 15% drawdown produces `flatten_account` and `flatten_division` fires on both paths; (b) complete B1 Phase-6 live validation (#2); (c) demonstrate the kill path (#3) end-to-end; (d) a paper-mode soak confirming the autonomous-live audit/monitoring (the `hitl_activity_24h` red tile is expected — re-tune it as the new normal). Success criterion: a forced 15%-drawdown scenario auto-flattens with no human, and a real fill shows the B1 stop resting server-side.

## Appendix — reproducibility
HITL surface + removal trace, and the per-breaker verification, were mapped by two read-only sub-agents and spot-verified by primary read of the load-bearing lines: `models.py:352-355` (`drawdown_pct`), `observer:1518/3247` (`peak_equity=equity`), `main.py:2516-2518` (documented zero-drawdown), `observer:234/2701-2812` (the gate), `data_exec.py:412-536` (`flatten_division`), `bitunix.py:973-982` (B1 body), `risk.yaml` (limit values), `main.py:153-162` (`confirm_live`).
