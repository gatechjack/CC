# Post-funding-units diagnostics — 2026-05-21

Three back-to-back diagnostics run after the funding-units fix deploy (commit
`4f04fa66`, deployed 2026-05-21 13:05 UTC). Trigger: the first post-deploy
multi-TP win surfaced an internal-contradiction signature (`result_ts` < `ts`).
Investigations ran sequentially; each one's verdict reframed the next. Final
state recorded here for the 60-day-clock audit trail and to retire two
caveats.

## 1. Trade `2942ff8e` — reality-verified (caveat retired)

First v2 multi-TP win in prod. Closed 2026-05-21 ~14:00 UTC. Short
BTC/USDT.P.

**Recorded:** entry 77089.4, original SL 77324.2447, TP1 76950.639, TP2
76854.555, TP3 76502.288 (unfilled). Filled legs `["tp1","tp2"]`. Recorded
`actual_r_multiple = 0.7955`. Recorded `result_ts = 14:00:00`,
`bars_to_resolution = 1` — these two values are cosmetic artifacts of the
finalizing replay tick's path-attribution; the exit price and R are correct.

**Bar-by-bar reconstruction from `bitunix_bar_history` (3m, BTC/USDT.P):**

| Bar (UTC) | O | H | L | C | Event |
|---|---|---|---|---|---|
| 14:00 | 77090.1 | 77193.9 | 77073.0 | 77166.8 | Entry plausible (77089.4 ∈ [L,H]); no TP yet |
| 14:03 | 77166.8 | 77215.5 | 77112.4 | 77126.1 | Counter-trend up; no TP |
| 14:06 | 77126.1 | 77131.8 | 77008.0 | 77065.0 | Drift down; no TP |
| 14:09 | 77065.0 | 77174.2 | 77055.0 | 77055.6 | No TP |
| 14:12 | 77055.6 | 77120.7 | 77008.7 | 77054.6 | No TP |
| **14:15** | 77054.6 | 77061.8 | **76888.0** | 76956.4 | **TP1 hit** (L 76888.0 ≤ 76950.639) |
| **14:18** | 76956.4 | 76966.9 | **76780.3** | 76780.6 | **TP2 hit** (L 76780.3 ≤ 76854.555) |
| 14:21 | 76780.6 | 76914.0 | 76747.1 | 76872.0 | Runner survives (H 76914.0 < ratcheted SL 76950.639) |
| 14:24 | 76872.0 | 76943.1 | 76867.5 | 76907.8 | Survives (H 76943.1 < 76950.639) |
| **14:27** | 76907.8 | **77026.1** | 76907.8 | 77023.1 | **Runner SL hit** (H 77026.1 ≥ 76950.639) |

**R reconstruction from OHLC (not from recorded prices):**

- TP1 leg: `0.25 × (77089.4 − 76950.639) / 234.845 = 0.14770`
- TP2 leg: `0.50 × (77089.4 − 76854.555) / 234.845 = 0.50000`
- Runner @ ratcheted SL 76950.639: `0.25 × (77089.4 − 76950.639) / 234.845 = 0.14770`
- **Total = 0.79540 ≈ recorded 0.7955**

Every leg reconstructs from real bar OHLC, including the runner's exit.
**Status: reality-verified.** This trade counts cleanly toward the 60-day
clock sample. The earlier internal-consistency-only caveat is retired.

**Cosmetic artifact (not a bug):** `result_ts = 14:00:00 < ts = 14:00:12` is
a two-tick replay artifact. Tick 1 walked bars to ~14:22 wall-clock, emitted
SL-update audits with backdated `ts = bar_ts_iso`, persisted `filled_legs`
and ratcheted `current_sl=76950.639` to `extra_json` with `still_open`. Tick
2 re-fetched from entry, saw bar 0 (14:00:00) has H ≥ ratcheted SL → closed
on bar 0 with `result_ts = 14:00:00`, `bars_to_resolution = 1`. The
exit-price attribution is correct (runner did exit at 76950.639). The
path-attribution timing is misleading — see B5.

**SL-update events fired correctly per spec.** post_tp1: 77324.245 →
77089.4 (entry/BE). post_tp2: 77089.4 → 76950.639 (TP1 price). Both
`audit_event` rows carry `order_id=2942ff8e…`. These are the first-ever
firings of `position_sl_update` in prod; lifecycle is spec-correct.

## 2. Reconciler bar-source audit — sound under current coverage

`scripts/audit_reality_reconciler.py:_load_bars_for_trade` (lines 80–93)
reads `bitunix_bar_history` directly with default `timeframe='3m'`. **No
BitUnix kline API fallback. No cached/pickled alternative.** Writer is
`BitUnixBarArchiver.archive_once()`
(`trading_corp/data/bitunix_bar_archiver.py`), a 60s background loop
draining `LiveBarCache` for BTC/USDT.P across 3m/1h/4h/1d (no 1m archived —
`SELECT COUNT(*) FROM bitunix_bar_history WHERE timeframe='1m'` returns 0).

**Coverage state at audit time (2026-05-21 ~17:30 UTC):**

| Day | 3m bars (expected 480) |
|---|---|
| 2026-05-15 | 370 (partial day — process start) |
| 2026-05-16 → 2026-05-20 | 480 each (full) |
| 2026-05-21 | 472 (in-progress, 20/hr through 22:00 UTC) |

The 2026-05-21 12:00–16:00 UTC window — flagged in the first diagnostic as
having no bars — actually has all 80 expected 3m bars (`SELECT COUNT(*)
WHERE timeframe='3m' AND ts_ms BETWEEN …` returned 80). Archiver coverage
is continuous.

**The 2026-05-21 06:03:42 UTC unattended reconciler fire** matched 3/3 v2
trades:

| order_id | window | 3m bars covering window |
|---|---|---|
| `35aa49c9…` | 2026-05-18 16:24 → 2026-05-19 05:44 (~13.3h) | 266 |
| `a467e316…` | 2026-05-18 18:30 → 2026-05-19 07:50 (~13.3h) | 266 |
| `ef6e6697…` | 2026-05-20 16:30 → 2026-05-20 16:38 (~8 min) | 2 |

All 3 matches were against present bar data. The "3/3 clean" track record
is genuine reality-reconciliation, not phantom. Immune system sound under
current archiver coverage.

**Structural fragility (not a current failure):** the reconciler has no
`len(bars) == 0` guard before declaring match. Empty bars currently produce
sim `result="expired"` from the classifier; since all current trades
record `loss` or `win`, this produces a mismatch (correct). But if any
trade is ever recorded as `result="expired"` AND bars are absent → sim=
expired AND rec=expired → match-against-zero-bars. That's the kline blind
spot rebuilt inside the immune system. Backlog as B7 (do-soon).

## 3. Premise-conflict case study — `[[verify-premises-against-ground-truth]]` 5th instance

Diagnostic A (certifying `2942ff8e`) concluded that `bitunix_bar_history`
had no 3m or 1m bars for 2026-05-21 12:00–16:00 UTC, and therefore graded
the trade "internal-consistency-only" rather than "reality-verified."
Diagnostic B (reconciler validity) ran 90 minutes later and observed 20 3m
bars per hour through that exact window. One SQL query (`SELECT COUNT(*)
FROM bitunix_bar_history WHERE timeframe='3m' AND ts_ms BETWEEN …`)
settled it: 80/80 expected bars present. B was correct; A was wrong about
3m. (A was incidentally correct that 1m bars don't exist — the archiver
writes no 1m timeframe — but the load-bearing 3m claim was false.)

**Mechanism of A's error:** A's text reads:

> *"Bar-OHLC verification is not possible from stored data:
> bitunix_bar_history has no 3m or 1m bars for 2026-05-21 12:00–16:00 UTC
> (the table only stores bars the live bar cache ingests for the
> reconciler's ATR; the replay fetches bars live from the BitUnix kline
> API and discards them)."*

A reasoned from a code-path premise — "the replay fetches from the kline
API and discards bars" → therefore `bitunix_bar_history` doesn't have
these bars — without running the verifying query. A even named the actual
writer (the live ATR / reconciler path) in the same sentence, but didn't
follow through to check whether that writer had covered the window. Two
minutes of SQL would have refuted the claim.

**The distinguishing cost of this instance.** Earlier instances of the
pattern (kline, regime, funding) had a wrong-premise → wrong-number
shape. This instance had a wrong-premise → **correct-number-with-false-
epistemic-grade** shape. A's reading of `2942ff8e`'s R happened to be
right (the trade really did reconstruct to 0.7955); the damage was the
trust label A applied. Internal-consistency-only is a strictly weaker
grade than reality-verified, and for a clock-sample trade, that
grade-degradation matters: it implies the system can't independently audit
a sample row, which (if true) would shadow every subsequent clock-grade
audit. An unverified premise can corrupt confidence even when it doesn't
corrupt the answer. That version of the lesson is the hardest to catch
because nothing observable looks wrong.

**Forward rule (clarification, not new):** before grading a result on a
presence/absence claim about stored data, run the SELECT. Especially when
the absence claim is reasoning-derived rather than query-derived.

---

## Backlog items raised this session (now in BACKLOG.md)

- **B7 — DO-SOON.** Reconciler missing `bar_count > 0` guard before
  declaring match (rebuilt-blind-spot risk on `expired`-outcome trades).
  One-line fix shape: explicit `audit_reality_no_bars` outcome that can
  never equal a match.
- **B6 — LOW.** Reconciler API-refetch path. Downgraded from prior
  write-up; archiver coverage confirmed continuous. Load-bearing only if
  archiver coverage degrades.
- **B8 — LOW (latent).** Reconciler query filters `bitunix_bar_history`
  on `timeframe` only, not `symbol`. Table has no `symbol` column. BTC-
  only today; two-part fix when multi-symbol futures land.
- **B5 — cosmetic.** `bars_to_resolution` records finalizing-tick bar
  index, not total bars walked. Display-only.

## Memory updated this session

`feedback_verify_premises_against_ground_truth.md` — appended bar-coverage
as 5th confirmed instance, with the wrong-premise-yields-correct-number-
with-false-grade note. Core rule unchanged.
