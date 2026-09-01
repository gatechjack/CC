# PEAD off-hours single-outcome failures — fixture-brittle vs regression (2026-09-01)

READ-ONLY. Sources: [TEST] box `tests/test_pead_offhours_single_outcome.py`; [CODE] deployed
`pead_strategy.py` sha256 `28eb62be` (materialized, unmodified); [RUN] pytest on an isolated
box tree copy (venv, py3.12, pytest 9.0.3, asyncio plugin loaded).

## VERDICT: FIXTURE-BRITTLE (stale test double). Feature INTACT. NOT date-sensitive.
The prior "date-sensitive" read is **REFUTED**. Root cause is a stale fake-broker signature,
and it fails on **every** date, not just 2026-09-01.

## 1. WHY they fail (TEST, not CODE)
[CODE] deployed `manage()` :656 calls `last = float(await broker.quote(r["symbol"], strict=True))`
(the `strict=True` was added by the Part-3 rename-defense). It is wrapped:
`except QuoteSymbolUnresolved: ... except Exception as e: log.debug(...); continue` (:667).
[TEST] the fake double is `class _FakeBroker: async def quote(self, symbol): return self.price`
(:50) — it has **no `strict` parameter**. So `await broker.quote(sym, strict=True)` raises
`TypeError: quote() got an unexpected keyword argument 'strict'`, which is swallowed by the
generic `except Exception → continue`. The per-symbol loop skips the row **before** any exit
rule is evaluated → no `pead_exit_deferred`, no drift exit.

Exact failing assertions [RUN, original fixture]:
- `test_deferred_is_not_terminal_then_exactly_one_exit_at_open` :114 —
  `assert logger.kinds() == ["pead_exit_deferred"]` → **actual `[]`** (TIME never evaluated).
- `test_drift_marker_not_consumed_pre_open_fires_once_at_open` :173 —
  `assert len(exits) == 1` (session tick) → **actual `0`** (DRIFT never evaluated).

(The `does-not-exist.yaml` WARNING in the captured log is by-design — the test uses a missing
config so `_cfg()` returns {}. The real trigger — the swallowed `TypeError` — logs at DEBUG,
which pytest doesn't surface by default, which is why it looked mysterious.)

## 2. Distinguishing check (neutralize the actual dependency)
Ran a fixture-corrected trace against the **unmodified deployed code** — the ONLY change vs the
box test is `async def quote(self, symbol, *, strict=False)` (matches the deployed call):
- `test_rootcause_is_the_strict_kwarg` — **PASS**: `strict` absent from the box fixture's quote
  signature; calling it as manage() does raises `TypeError`; with that fixture manage() emits no
  deferred event (reproduces the red).
- With the corrected fixture, both scenarios **PASS** (see §3).
The distinguishing variable is the **broker quote signature, not the date**. Date is a red
herring: the tests use relative offsets (`today`, `yesterday`, `_prev_weekday(today)`,
`opened_days_ago`) and the code's own `_prev_weekday`, so they are date-independent; the
`strict` mismatch breaks them uniformly on all dates.

## 3. The exactly-one-exit PROPERTY HOLDS on deployed code (the thing that matters)
[RUN] fixture-corrected, against deployed `28eb62be` — **3 passed**:
- `test_property_deferred_not_terminal_exactly_one_exit` — **PASS**: pre-open TIME exit is
  DEFERRED (logged `pead_exit_deferred`, rule=time, reason=pre_open) and is **NOT terminal**
  (row stays `result IS NULL`) across two pre-open ticks; at the open it places **EXACTLY ONE**
  real sell (`pead_exit` ×1, row → win/loss); a further session tick fires **nothing** (no
  double-exit, no half-open row).
- `test_property_drift_not_consumed_pre_open_fires_once` — **PASS**: drift is **not evaluated /
  not consumed** pre-open (no fetch, marker stays NULL, no event); at the open drift fires
  **exactly once** (`pead_exit` ×1, rule=drift), the marker advances exactly once to yesterday,
  and a further tick does not re-fire or re-fetch.

So: no double-fire, no lost exit, no half-state. The off-hours single-outcome guarantee is
real on current deployed code.

## 4. Real regression? NO.
No code regression in the deferred/drift/off-hours-gate path. The off-hours gate and the
deferred/drift-marker logic behave correctly (§3). What changed is Part-3 adding `strict=True`
to the `manage()` quote call, which broke the **test double** (never updated), not the feature.
No live position is exposed — the exactly-one-exit property holds on the deployed code.

Only this one fixture is affected: `_FakeBroker.quote` in `test_pead_offhours_single_outcome.py`.
The rest of the box PEAD suite passes (70/72; the 2 reds are exactly these). The trivial, TEST-ONLY
fix (not done here — findings only) is to add `*, strict=False` to that fake broker's `quote`.

## 5. Blast radius on tonight's snapshot-guard deploy: NONE.
Independent code paths: the snapshot guard wraps `broker.snapshot()` (which succeeds in these
tests); these failures are at `broker.quote(..., strict=True)`. The snapshot patch does not
touch the `strict=True` quote call, does not fix or worsen these tests (regression run: patched
73/2 vs pristine 70/2 — same 2 reds, +3 new). Deploying the snapshot guard neither locks in nor
interacts with this stale fixture. Note only: the box PEAD suite will keep showing these 2 reds
after the snapshot-guard deploy until the fake-broker fixture is updated — they are a known
TEST-only artifact, not a deploy regression.

## Evidence
`_stage_test_offhours_diag.py` (committed alongside as `pead_offhours_diag.py`) — the
fixture-corrected trace; 3 passed against deployed `28eb62be`.
