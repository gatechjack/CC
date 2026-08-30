# Shard Money-Management — RUNG LADDER (build-order #1; Option B ruled)

**Date:** 2026-08-30 · **Branch:** `pm-shard-scope-2026-08-30` · Converts `SHARD_MONEYMGMT_SCOPING_2026-08-30.md`
(Option B ruled: Kalshi-native `target_balance_allocation` as the mover; shard-aware read FIRST/load-bearing;
explicit `exchange_index` as hygiene; A fallback; C deferred) into rungs. **Empirical map (probe 17:18Z): shard 3 =
mlb/atp/wta/tennis; shard 0 = the other 11; none on 1/2 — a clean shard-0↔shard-3 problem.** All Jack's money is on
shard 3 today, so the 11 shard-0 categories are unfundable until this works.

Each rung: build only, box-scratch green, live untouched, adversarial review, SHA, push, HALT.

---

## THE LADDER

### ★ Rung 1 — Shard-aware balance READ  *(THIS BUILD; load-bearing, first)*
`prediction_markets/shard_balance.py` — pure-stdlib, imports NOTHING from the order path. Parses
`GET /portfolio/balance`'s `balance_breakdown` (already in the response, ignored today) into `ShardBalances`:
`total_dollars`, `by_shard{idx->$}`, **`has_breakdown`**, `shard(idx)`, `can_fund(idx, need)`, `shard_sum()`.
**Fail-safe contract:** breakdown absent (subaccount-restricted key) → `has_breakdown=False`, `shard()`/`can_fund()`
return **None** = "cannot verify → do NOT place." Strict: a malformed breakdown entry RAISES (never a silently-wrong
split). `fetch_shard_balances(client)` = a 3-line async wrapper over the raw `client.get('/portfolio/balance')`
(the R7-probe-proven call). Tests (`test_shard_balance_r1.py`) pin, per R4, that **the required value (shard-3) ≠
the wrong value (the masked total)** — the Karen-death shape ($515 total masking $2.45 on shard 3) asserts
`can_fund(3, $5) is False` while the total is 100× it. No live touch, no order-path file changed.

### Rung 2 — Thin-shard chokepoint guard  *(the design question — proposed below; touches the order path → full adversarial review)*
A pre-flight shard-funding gate at `execution.evaluate`, using rung 1's read. See "THE THIN-SHARD SHAPE" — Jack rules.

### Rung 3 — Explicit `exchange_index` on the order body  *(hygiene; touches the SHARED order-body builder → adversarial review)*
`build_v2_event_order` currently omits `exchange_index` and relies on Kalshi auto-routing to the market's shard.
Set it explicitly (the market's `exchange_index`, or `-1` = force auto-route) for **deterministic** routing. **NOT
"clean" to fold into rung 1** (rung 1 is read-only; this mutates the shared V2 body used by legacy poly_kalshi_mlb
too). Must be behavior-neutral for legacy (`-1`/omitted ≡ auto-route) and adversarially reviewed on its own.

### Rung 4 — `target_balance_allocation` mechanism  *(the money-mover; Jack fires, not me)*
Build + validate the `POST /portfolio/target_balance_allocation` body for our shard-0↔shard-3 split (a two-entry
`[{0:X},{3:Y}]`), prove it offline, and **hand Jack the exact call** — it is a WRITE that moves real money, verified
with **small amounts first** (the ruling). Carries the two caveats: **"sweepable balance" is undefined** and **a
target % is ALSO A CAP** → verify against the live API, do not trust the docs. This is the rung that unblocks
promoting outside {mlb, atp, wta, tennis}.

### Rung 5 — Surface the per-shard split  *(overlaps build-order #5 account pages)*
Display `by_shard` + a `has_breakdown=False` warning on the account/exposure view, so a drained shard is VISIBLE to
a human, not only to the guard. Deferred to the account-pages work.

---

## ★ THE THIN-SHARD SHAPE — "what should the platform do when a shard is too thin to fund an order?" (Jack rules)

**Today:** the order auto-routes to the market's shard, Kalshi returns a **400**, the driver cycle **errors**. A
single thin-shard 400 just fails that cycle *silently* (this is how Karen's division died); only **≥3 consecutive**
order errors trip the auto-latch, so a persistent-but-not-3-in-a-row funding gap can bleed unnoticed.

**Options:**

- **(i) Pre-flight shard-funding gate at the chokepoint — RECOMMENDED.** A new gate in `execution.evaluate` (a
  "gate 6b"), BEFORE placement: read the market's shard via rung 1; if `can_fund(order_shard, notional)` is **not
  True** → return `skip:shard_underfunded` (a **labelled SKIP**, not a reject, not an exchange error) + a **surfaced
  WARNING** log. Fail-safe: `can_fund` returning **None** (split unknown, `has_breakdown=False`) also skips — never
  place blind. *Why:* it makes the condition VISIBLE (a labelled skip beats a silent 400 — the same lesson as the
  search "labelled skip, no silent drop"); it keeps the kill-switch's error-latch semantics CLEAN (a funding gap is
  fundable-later, not a fault, so it must not count toward the ≥3-error latch); and it does not halt trading on a
  transient gap. With rung 4's allocation set, a drained shard auto-refills in ~10 s so the skip is transient; with
  no allocation, the skip persists until an operator moves money.
- **(ii) Auto-disarm/latch on thin shard — NOT as the primary.** A thin shard is a fundable-later condition, not a
  fault; latching would halt all trading on a transient gap and needs `--clear-latch` to resume. Reserve latching
  for genuine faults. BUT a **sustained** underfunding (N consecutive `skip:shard_underfunded` on the same shard)
  should escalate to a **surfaced alarm** (not a latch) so a human moves money — the anti-silent-bleed backstop.
- **(iii) Warn-but-still-attempt — REJECT.** Closest to today: the 400 still fires, the cycle still errors, the
  latch accounting is still wrong. A warning helps but does not fix the silent-failure or the fault-miscount.

**Recommendation (Jack rules):** **(i) the pre-flight gate as a labelled skip + surfaced warning, fail-safe on
`has_breakdown=False`, plus a separate sustained-underfunding alarm (not a latch).** It makes Karen's silent death
structurally impossible, keeps fault semantics clean, and layers correctly under rung 4's auto-refill. Rung 2 builds
whichever shape Jack rules.

---

*Rung 1 is built in this commit. Rungs 2-5 each their own authorization. [[kalshi-exchange-sharding-2026-08-30]]
[[prediction-markets-backlog]]*
