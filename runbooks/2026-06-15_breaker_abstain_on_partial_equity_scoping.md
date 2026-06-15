# Scoping: drawdown breaker must ABSTAIN on a partial / under-reported equity read

**Status:** ANALYSIS + DESIGN ONLY — no code, no deploy (CLAUDE.md §4). Read-only investigation.
**Date:** 2026-06-15
**Priority:** ahead of #2 (signed-fetch auto-book) — this is a live-money safety bug.
**Surfaced by:** the 10006 rate-limit fix (branch `bitunix-10006-ratelimit-fix-2026-06-15`, commit `b0ae39d`).

---

## 1. The bug (live-money safety)

A single BitUnix `10006` ("request too frequently") on the **high-balance stablecoin**
makes `BitunixBroker.snapshot()` **drop that coin** and **under-report total equity** —
e.g. it reads ~$25 (USDT only) instead of the real ~$3,382 (USDT+USDC). The drawdown
breaker computes `(peak − equity)/peak` against the (protected) high-water mark, so an
under-read of ~$25 vs a peak of ~$3,382 = **~99% apparent drawdown ≥ the 15% cap →
`flatten_account=True` → the account is flattened on a phantom drawdown caused by a
transient API error.**

Overnight 2026-06-15 it was harmless **only because the account was flat** — the 7
`flatten_account_noop_already_flat` rows may be exactly these false fires landing as
no-ops. With a live position open during a coincident 10006, the breaker could flatten a
real (possibly winning) position at the worst possible moment.

The 10006 fix reduces how often the partial read happens and refuses to *cache* it
(strictly safer), but it does **not** make the breaker **abstain** on a partial read.
"Don't flatten on incomplete data" is the missing safety rule.

---

## 2. How the breaker (mis)handles a partial read TODAY

Verified chain (all `bitunix_futures_observer.py` unless noted), two alert-gated sites:

| Step | v2 path | legacy path | what happens on a partial read |
|---|---|---|---|
| fetch | `snap = await broker.snapshot()` :1443 | :3271 | returns under-counted equity, **no signal it's partial** |
| equity | `account_equity = float(snap.equity …)` :1444 | :3272 | `account_equity ≈ 25` (passes the `<= 0` guard) |
| HWM | `_tracked_peak_equity(account_equity)` :1532 | :3327 | `max(stored_peak, 25)` → peak **unchanged** (protected, :2467) |
| evaluate | `risk_agent.evaluate(order, account, …)` :1534 | (same) | `risk.py` |
| breaker | `risk.py:177` `if account.drawdown_pct() >= max_dd` → `flatten_account=True` :181 | — | `(3382−25)/3382 ≈ 0.99 ≥ 0.15` → **flatten** |
| dispatch | `_maybe_flatten_on_risk_verdict(verdict)` :1551 → `data_exec.flatten_division("bitunix_futures")` :2380 | :3345 | flattens the live account |

`drawdown_pct()` = `max(0, (peak_equity − equity) / peak_equity)` (`persistence/models.py:385`).
`per_account_max_drawdown_pct` default `0.15` (`risk.py:176`).

**Key root cause:** the *completeness* of the equity read is **not visible** to the breaker.
The `AccountSnapshot` it receives (`brokers/base.py:36`) is just
`account, equity, buying_power, cash, positions` — no field says "one stablecoin failed,
this equity is partial." The breaker structurally **cannot** distinguish
"equity genuinely fell 99%" from "I only read one of two coins."

> Groundwork already done in `b0ae39d`: `BitunixBroker._fetch_snapshot()` already computes a
> `complete` flag (set False when a stablecoin balance read OR the position read returns a
> non-zero code) and already **refuses to cache a partial**. That flag is currently internal —
> it is consumed only by the cache, not surfaced to callers. This fix **surfaces and acts on it.**

---

## 3. Design

### 3a. Surface a completeness signal on the snapshot
Add one backward-compatible field to `AccountSnapshot` (`brokers/base.py:37`):

```python
equity_complete: bool = True   # False ⇒ a balance source errored; equity is UNDER-reported
```

- Default `True` → every other broker (robinhood/tasty/coinbase/polymarket/kalshi/paper)
  and every existing constructor is unaffected (single-source equity = always "complete").
- `BitunixBroker` sets it from an **equity-specific** completeness = "all stablecoin
  (`_STABLE_MARGIN_COINS`) balance reads returned code 0." Distinguish from the existing
  combined `complete` flag: a **position**-read error should NOT make the *equity* breaker
  abstain (positions don't affect equity; the reconciler owns position-read errors). So
  track stablecoin-completeness for `equity_complete`; the cache may keep using the broader
  `complete` for its no-cache-partial rule.

### 3b. The abstain rule (the safety rule)
**The breaker MUST NOT act on `flatten_account` when `snap.equity_complete is False`.**
Precise statement:

> Abstain **iff** the equity read is incomplete. On a **complete** read, behave exactly as
> today — a genuine ≥15% drawdown still flattens (the safety net is preserved). The abstain
> is conditioned **only** on incompleteness, never on the magnitude of the drawdown.

**Recommended realization (cleanest, both paths):** in the observer, immediately after
`account_equity` is derived, if `not snap.equity_complete` → **skip this evaluation**
(log a `breaker_abstain_incomplete_equity` audit + a score/decision note) and `return`
before building `AccountState` / calling `evaluate`. This inherently makes the breaker
abstain **and** avoids feeding the under-reported equity into tier-sizing (a secondary
issue — see §5). Centralize in one helper so the v2 (:1443) and legacy (:3271) paths can't
diverge.

**Narrower alternative (if sizing-on-partial behavior must be preserved):** guard only the
flatten dispatch — pass completeness into `_maybe_flatten_on_risk_verdict` (:2358) and
no-op the flatten when incomplete. This blocks the false flatten but still lets a
mis-sized (under-sized) entry through and logs a spurious phantom-DD reject. Not
recommended — under-reported equity is wrong for *every* downstream use, not just flatten.

### 3c. New audit kind
`breaker_abstain_incomplete_equity` with payload `{equity_read, missing_sources, peak_equity,
would_be_drawdown_pct}` so the operator can see how often the breaker abstained and what
the phantom drawdown *would* have been (this also quantifies how close we came to a false
flatten historically once deployed).

---

## 4. Failure-mode check — is abstaining safe? (the hard-stop)

**Yes — abstaining on an incomplete read is strictly safer than the status quo.**

1. **Brief blindness recovers next tick.** An incomplete read is transient (a single
   10006); the next alert/tick gets a complete read and the breaker resumes. A real
   drawdown is not a sub-second event — it survives to the next complete evaluation.
2. **Per-position server-side stops (B1) are the primary protection and are unaffected.**
   The account-drawdown breaker is a *secondary, account-level* net. Each open position
   already carries a catastrophic stop **on the exchange** (B1, entry-attached). Even if the
   account breaker abstains for a window, every position is still protected by its own
   server-side stop regardless of bot/breaker state.
3. **The risk is one-directional → abstain only ever *prevents* a false flatten, never
   causes a missed real one on a complete read.** Dropping a coin can only *under*-report
   equity (fewer coins summed) → only *phantom-high* drawdown. And the HWM is protected by
   `max(stored_peak, current)` (:2467), so an under-read can't corrupt the peak either. The
   abstain rule fires only when the read is incomplete; a complete read showing genuine
   ≥15% drawdown is untouched and still flattens.

**Bounded residual to handle (recommended, in-increment):** a *sustained* 10006 storm could
keep equity perpetually incomplete → the account breaker stays blind for the storm's
duration (per-position B1 stops still hold). To avoid *silent* indefinite blindness, emit a
telegram/operator escalation if `equity_complete` is False for **N consecutive** breaker
evaluations (e.g. N=5). This makes "the account breaker is currently degraded" visible
rather than silent. (Note: a partial read still sets `_last_successful_snapshot_ts`, so the
existing stale-snapshot order-path halt will NOT trip on partials — escalation is the right
signal here, not the staleness halt.)

**Hard-stop respected:** the design never blinds the breaker to a *real* drawdown from a
*complete* read — abstain is conditioned on incompleteness alone.

---

## 5. Out of scope / related (note, don't fix here)

- **Sizing on a partial read.** Under-reported equity also under-sizes the next entry
  (tier-sizing reads `account_equity`). This is *conservative* (smaller bets), not
  dangerous — but the recommended §3b "skip evaluation on incomplete equity" covers it for
  free. If the narrower alternative is chosen instead, sizing-on-partial remains.
- **Position-read completeness.** A 10006 on the position endpoint yields an empty position
  list, which the reconciler/flatten-verification already handle on their own path; it does
  not affect equity, so it's out of this fix's scope.
- **Making the read itself complete** (retry the errored coin) — a different lever; the
  10006 fix already lowers frequency. Abstain is the safety floor regardless.

---

## 6. Build increment (thin, testable) — NOT built here

One increment:
1. `AccountSnapshot.equity_complete: bool = True` (`brokers/base.py`).
2. `BitunixBroker` sets `equity_complete` from stablecoin-read success (extends the
   existing `b0ae39d` completeness tracking; split equity- vs position- completeness).
3. Observer abstains on `not snap.equity_complete` at both snapshot sites via one helper +
   `breaker_abstain_incomplete_equity` audit.
4. (Recommended) escalation on N consecutive incomplete reads.

**Tests (mocked/fundless, mirror `tests/test_bitunix_snapshot_cache.py` harness):**
- partial read (one stablecoin 10006) → breaker **abstains**, `flatten_division` **NOT**
  called (assert), audit row written.
- **complete** read showing genuine ≥15% drawdown → breaker **flattens** (safety net
  preserved — the critical non-regression).
- complete read, healthy equity → no action.
- (if implemented) N consecutive incomplete reads → escalation fires once.

**Dependency / sequencing:** build on top of `bitunix-10006-ratelimit-fix-2026-06-15`
(`b0ae39d`) — it already computes completeness and refuses to cache partials. Either branch
off it, or land it after b0ae39d merges, to avoid re-implementing the completeness tracking.

---

## 7. Open decision for the operator

- **§3b realization:** skip the whole evaluation on incomplete equity (recommended —
  covers breaker + sizing) **vs.** guard only the flatten dispatch (narrower).
- **Escalation N** (consecutive-incomplete threshold before paging) — recommend 5; confirm.
