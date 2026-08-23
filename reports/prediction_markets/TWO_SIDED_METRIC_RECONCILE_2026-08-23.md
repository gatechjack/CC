# Two-sided metric reconciliation — the P1 record carries TWO different "two-sided %" figures (2026-08-23)

**Why this doc exists (Jack, CP1):** a load-bearing caveat metric appears under one name with two different values in the
P1 record. Recorded so nobody later "corrects" a right number against a wrong reference — the same class of trap as the
notional-vs-cost ROI confusion (§13A(g)). **Material impact: none** — a whale that is two-sided by either measure is
uncopyable either way. But an undocumented ambiguity in a load-bearing metric is exactly what this build keeps getting
bitten by, so it is written down.

## The two metrics

- **Metric I — `P1_PLAN §13A(i)`** (and what the probe/`[4d]`/CP1 measured): **`two_sided_pct = n_two_sided / n_condition_ids`**,
  i.e. of a whale's DISTINCT condition_ids, the fraction held on more than one `outcome_index`. §13A(i) states it exactly:
  "Kickstand7 = 489 two-sided markets of 1314 distinct (~37%)."
- **Metric J — `P1_PLAN §13A(j)`** (the "Material:" list): a DIFFERENT, systematically HIGHER figure. §13A(j) lists
  "BetMechanic 71%, FordBronco 70%, Kickstand7 46%, 4751346 41%, Kh4mz4t 38%." **Its exact denominator/method is NOT
  documented anywhere in the P1 record** — that undocumented method IS the ambiguity.

## P2 implements Metric I (the spec definition), and it reproduces §13A(i) to the exact count
`P2_PLAN §5.1` defines `two_sided_pct = n_two_sided/n_condition_ids` = Metric I. The migration-004 rollup implements
exactly that. The read-only `[4d]` characterization (2026-08-23) reproduced §13A(i) **to the exact count**:
Kickstand7 `0xd1acd3925d89` = **1314 distinct / 489 two-sided = 0.3721**. That exact reproduction is what proves the
code is correct AND what exposed that §13A(j) is a different metric.

## Side by side (spec/Metric-I measurement 2026-08-23 vs the §13A(j) figure)

| whale | wallet (trunc) | Metric I (spec, measured) | §13A(j) figure | 
|---|---|---|---|
| Kickstand7 | `0xd1acd3925d89` | **0.3721** (489/1314) | 46% |
| BetMechanic | `0xa6a856a8c8a7` | **0.6844** (6930/10126) | 71% |
| FordBronco | `0x75e091ca3f8e` | **0.6929** (88/127) | 70% |
| Kh4mz4t | `0x52f454c43b23` | **0.3377** (77/228) | 38% |
| 4751346 | (not mapped here) | (in the `[4d]` per-wallet table) | 41% |

Metric J is a **consistent few points above** Metric I across every whale — structural, not noise → two different
computations, not measurement error. (Kh4mz4t's wallet `0x52f454c43b23` was also confirmed via its ufc one-sided
count = 121, matching the P1 record exactly.)

## Ruling
- The **spec / Metric I** (`n_two_sided/n_condition_ids`, §13A(i)) is the one P2 ships in `two_sided_pct`. It is correct.
- Where the P1 docs cite a two-sided % (§13A(j): 46/71/70/41/38), those are **Metric J** and should NOT be used to
  "check" the P2 column — they are a different metric whose method the record does not preserve.
- `two_sided_pct` is computed **per (wallet, category)** in the rollup; the §13A(i)/§13A(j) figures are **per-wallet
  aggregates**. Do not compare a per-category cell against a per-wallet doc figure either.

Cross-ref: `P1_PLAN.md §13A(i)/(j)`, `P2_PLAN.md §5.1`, `FARM_RERANK_2026-08-23.md`, `P2_KICKOFF_2026-08-23.md`.
