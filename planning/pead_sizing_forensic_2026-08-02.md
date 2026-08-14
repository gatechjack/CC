# PEAD position-sizing forensic (read-only) — 2026-08-02

Base of record: worktree `claude-2026-08-02b` == `prod-live` == `dafe60b` (deployed code).
Account: Robinhood **680725082** ("Agentic", **cash** account, `divisions.yaml:66`).
Run-state: **LIVE + ACTIVE** — `divisions.yaml:71 standby:false` + `strategies.yaml:1919 auto_execute:true`.
All file:line below are in the worktree (== deployed).

## 1. The sizing formula — EQUAL-DOLLAR, `position_pct × equity`
- `pead_strategy.py:1019-1030` `_notional_budget(cfg, equity)`: if `position_notional` (fixed $)
  set -> use it; else `return position_pct * equity`. Docstring: "Equal-dollar notional per
  candidate (same value for every candidate in a scan)".
- `pead_strategy.py:1029` `position_pct = cfg.get("position_pct", _DEFAULT_POSITION_PCT)`;
  `:58` `_DEFAULT_POSITION_PCT = 0.10`. Config: `strategies.yaml:1950 position_pct: 0.10`.
  **No `position_notional` key in the PEAD config block** -> the `× equity` branch runs.
- `pead_strategy.py:416` `notional_budget = self._notional_budget(cfg, equity)` — computed
  **ONCE per scan, BEFORE the candidate loop**; never recomputed inside the loop.
- `pead_strategy.py:431` code comment: "equal-DOLLAR notional sizing (config-driven, same $
  per candidate)". Every candidate gets `notional_usd=notional_budget` (`:446`, `:462`).
- **Verdict:** EQUAL-DOLLAR. NOT percent-of-remaining. Every name in a wave is the SAME dollar
  amount. There is NO per-entry recompute of the size as BP depletes.

### "10% of WHAT?"
- `pead_strategy.py:413-414` `snap = await broker.snapshot(); equity = snap.equity`.
- `robinhood.py:513` `equity = float(profile.get("equity") or profile.get("extended_hours_equity") or 0)`
  from a LIVE `rs.profiles.load_portfolio_profile(account)` pull (`robinhood.py:495-498`).
- RH `/portfolios/` `equity` = **total account value = positions market value + cash**
  (NOT buying power, NOT cash-only, NOT stock-value-only).
- So the base is **total account equity**. 10% of that is each name's dollar size.

## 2. Buying-power source — used only as a SKIP guard, never for sizing
- `pead_strategy.py:415` `available_bp = getattr(snap, "buying_power", None)` (comment: "settled
  BP; None = no guard (paper/unknown)").
- `robinhood.py:514-520` `buying_power = margin_buying_power OR buying_power OR excess_margin
  OR withdrawable_amount`. LIVE pull. **Latent caveat:** it prefers `margin_buying_power`
  FIRST despite the "settled BP" label — but 680725082 is a CASH account (no margin field), so
  it resolves to the cash buying-power figure ($213.01). The caveat only bites if PEAD is ever
  pointed at a margin account.
- **Sizing does NOT read buying_power.** BP is consulted only at the skip guard (`:439`).

## 3. The floors (where a name is skipped), in the order they bite
1. `pead_strategy.py:432` `if notional_budget < 1.0: skip` — RH's $1 fractional min. Per-scan
   constant; if `0.10 × equity < $1` (equity < $10) NO name funds. Not binding today.
2. `pead_strategy.py:435-438` `broker.fractional_eligible(sym)` false -> skip (per-name).
3. `pead_strategy.py:439-442` `if notional_budget > available_bp: skip` — **the binding floor.**
   `available_bp` decrements per placed name (`:476` live intent, `:494` paper).
- **Effective count of fundable names** = `min(capacity, floor(available_bp / notional_budget))`.

## 4. `max_concurrent` — entry-only, zero effect on exits
- `pead_strategy.py:337` read; `:343` `capacity = max_concurrent - len(held)`; `:344-346`
  book-full early return; `:421-422` `if capacity<=0: break`; `:485`/`:505` `capacity -= 1`.
  ALL inside `scan()`.
- `:342` `held = _held_symbols() | _pending_symbols()`; `:791-801` pending includes
  `state IN ('pending','intent')` — queued/intent entries reserve a slot.
- `manage()` (exit engine, `:545+`) and `pead_pressures.py` (stop/drift/guard/time rules)
  contain **zero** references to `max_concurrent`/`capacity` (repo-wide grep confirms). Exits
  read `equity` (`:553`) for the risk gate only. **Confirmed: exits never read max_concurrent.**
- Config: `strategies.yaml:1951 max_concurrent_positions: 10`; default `:59 _DEFAULT_MAX_CONCURRENT=7`.

## 5. Reconcile vs stated requirement ("equal-dollar, 0.10 × equity, ~$234/name")
- **MATCHES.** Deployed code is equal-dollar `position_pct(0.10) × equity`. It is NOT
  percent-of-available-BP; names do NOT get smaller than the last. The only per-wave decrement
  is the internal `available_bp` counter (`:476`/`:494`), which feeds ONLY the skip guard
  (`:439`), never `_notional_budget`. The `$234/name` in the note implies equity ~$2,340 when
  written; today equity ~$2,203 -> ~$220/name. Mechanic identical; dollar figure moved with equity.

## 6. Worked example — LIVE balances (get_portfolio 680725082, 2026-08-02)
- equity (total_value) = **$2,203.12**; buying_power = **$213.01**; cash $419.81; positions $1,783.31.
- Per-name notional = 0.10 × 2,203.12 = **$220.31 — CONSTANT for every name (no decay).**

| Name in wave | Size computed | BP before | Guard `size > BP`? | Funded? |
|---|---|---|---|---|
| 1st  | $220.31 | $213.01 | 220.31 > 213.01 (short $7.30) | **NO — skipped (`:439`)** |
| 5th  | $220.31 | n/a | — | not reached (0 funded) |
| 10th | $220.31 | n/a | — | not reached |
| 15th | $220.31 | n/a | — | UNREACHABLE — `max_concurrent=10` caps capacity at 10 |
| 20th | $220.31 | n/a | — | UNREACHABLE — same cap |

**Headline:** at the current balance PEAD funds **0 new names** — the equal-dollar size ($220.31)
exceeds settled cash ($213.01), so the FIRST name is skipped by the BP guard. `max_concurrent=10`
is completely non-binding right now.

Illustrative (if settled cash were ample): every name is an identical $220.31 (1st = 5th = 10th),
proving equal-dollar / no decay; the 11th+ is blocked by `capacity<=0` (`max_concurrent=10`), so
the 15th/20th are never reachable. Full 10-name book would need ~$2,203 of settled cash.

### Robustness of the headline vs the one open ambiguity
The equity base is RH `/portfolios/ equity`, mapped to MCP `total_value` ($2,203.12). If instead
`equity` resolved to stock-value-only ($1,783.31): per-name = $178.33 -> 1st name funds, BP falls
to $34.68, 2nd skipped -> **1 name**. Either way the settled-cash guard caps PEAD at **0-1 new
names today**, and `max_concurrent=10` never binds. (Operator can confirm the exact per-name $ from
the engine's own `pead_intent`/`pead_entry` log `notional` field on the next live scan.)
