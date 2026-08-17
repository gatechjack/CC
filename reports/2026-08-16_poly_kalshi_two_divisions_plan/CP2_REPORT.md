# Phase 2a · CP2 — atomic `set_agent_state_multi` primitive (BUILT, not deployed)

> **LIVE-MONEY STATUS (leads):** `poly_kalshi_mlb` remains LIVE + ARMED, engine **untouched** (no
> restart, no prod mutation, no order placed). CP2 is branch-only code + tests. Shared byte-locked
> files byte-unchanged. Nothing wired into the boot path, endpoints, or the live loop yet — CP3/CP4
> consume this primitive.

## What was built

### 1. `set_agent_state_multi` — the atomic primitive (`trading_corp/persistence/db.py`)
```
def set_agent_state_multi(updates: Iterable[tuple[str, str, Any]],
                          db_url: str = "sqlite:///data/trading_corp.db") -> None
```
- Transaction boundary (`db.py:731-741`): one `conn.execute("BEGIN IMMEDIATE")` → per-row upsert →
  `COMMIT`; on ANY exception → `ROLLBACK` + re-raise. Empty `updates` is a no-op.
- Rows share one `updated_ts`. Reuses the exact upsert SQL of `set_agent_state` via a new small helper
  `_upsert_agent_state_row` (`db.py:693-711`). **`set_agent_state` itself is byte-unchanged.**
- Valid because `connect()` opens `isolation_level=None` (autocommit) → the explicit `BEGIN IMMEDIATE`
  starts a real transaction held until COMMIT/ROLLBACK (same pattern as `path_logger/store.py:119`).
- Supports the **3-key move shape** (the §1.5 fix): write `poly_kalshi_mlb/live_whales` AND clear from
  `polymarket_copy_trader/{selected_whales,pinned_whales}` in ONE transaction, so a promoted whale can
  never be silently re-added to paper by the weekly refresh.

### 2. Roster invariant helper (new own file `trading_corp/agents/strategies/roster_split.py`)
- `extract_wallets(value)` — normalizes any roster shape (list[dict] w/ `wallet`/`proxy_wallet`, bare
  strings, mixed) to a **lowercased** wallet set (case-insensitive identity).
- `assert_disjoint(live, paper)` → raises `RosterInvariantError` naming the offending wallet(s).
- `check_rosters_disjoint(db_url, ...)` — reads both keys, asserts `live ∩ paper == ∅`, returns the two
  wallet sets. **Built + tested; wired into nothing** (CP3 boot-assert + CP4 per-move consume it).

## Atomicity — PROVEN (tests/test_agent_state_multi.py, 8/8 pass)
- **Forced crash mid-move → rollback, no split state** (`test_forced_crash_mid_move_rolls_back_no_split_state`):
  monkeypatch makes row 1 (add to `live_whales`) really execute, then row 2 raises before COMMIT.
  Asserts `live_whales` reverted to empty and the whale stays in EXACTLY ONE roster (paper) — the split
  state (papering AND live) never persists.
- **Real DB error mid-move → rollback** (`test_real_integrity_error_mid_move_rolls_back`): row 2 with
  `agent=None` triggers a genuine NOT NULL IntegrityError (no fault-injection seam); row 1 rolls back.
- **3-key happy path** (`test_three_key_move_commits_all`): live_whales set, selected + pinned cleared,
  invariant holds.
- Plus: empty no-op; lock released after rollback (next move commits); `extract_wallets` shapes;
  `assert_disjoint` pass/raise (case-insensitive); `check_rosters_disjoint` from db.

## Verification
- **CP2 tests:** `pytest tests/test_agent_state_multi.py -p no:pytest_ethereum` → **8 passed**.
- **No regressions — empirically proven, not asserted:** ran the FULL suite on this branch AND on a
  throwaway baseline worktree at `386074c`; the sorted FAILED+ERROR sets are **byte-identical (92 each);
  `comm -13 baseline cp2` = EMPTY** → CP2 introduces **zero** new failures. The 92 pre-existing entries
  are all robinhood / tastytrade / webhooks / research modules (environmental gaps under local system
  Python 3.14 — missing optional deps + a pre-existing `FakeMacroExpert` import cascade), present
  unchanged on base. My CP2 tests are green within the full run; `roster_split` appears in zero failures.
- **Shared byte-locked files** (`kalshi_copy_trader.py`, `sports_team_mapping.py`, `kalshi_live.py`):
  `git diff 386074c` → **empty**.
- **Footprint:** `db.py` (+59/−1: the −1 is the `typing` import line), new `roster_split.py`, new
  `tests/test_agent_state_multi.py`. Nothing else.

## Not done (by design — later checkpoints)
CP3 (roster-split config + paper read-time subtract + boot invariant), CP4 (atomic paper⇄live endpoints
+ flatten-on-promote + pin-back & demote-open-live MUST-TESTs), CP5 (paper-Telegram kill + cutover .ps1),
CP6 (batched operator-run deploy + re-arm verify).
