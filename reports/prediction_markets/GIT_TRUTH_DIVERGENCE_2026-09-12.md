# PM / MACE GIT-TRUTH DIVERGENCE — reconcile handoff (2026-09-12)

**Standalone. Hand this to the reconcile agent cold — it answers "what diverged, whose is it,
and what cannot be fast-forwarded" without the conversation that produced it.**

All facts below were confirmed FROM GIT and from READ-ONLY box reads on 2026-09-12 ~03:00–03:15Z
(git fetch + rev-parse/merge-base/diff; box CR-stripped file hashes via
`cc/pm_divergence_boxhash_ro.ps1`). No writes, no deploys, no restart, no arm change, prod-live
NOT advanced.

> ⚠️ THIS SUPERSEDES one claim in `[[deploy-base-boxtruth-divergence-2026-09-11]]`: that memory
> was written when `origin/prod-live = 7220e32f`. prod-live has since advanced to `b1c552b1`,
> which **does** contain the MACE-sizing commit `6a5ed04`. The FF-block is now the *later* MACE
> deploys, not `6a5ed04`. See "Ancestry facts" below.

---

## TL;DR

- `origin/prod-live = b1c552b15005f2952a3a465e0a4894e7904f1993`.
- `origin/main = 61de372f…` and carries **0** files under `prediction_markets/`.
- The **box HEAD is not a single commit**. It is a FILE-LEVEL GRAFT of two lines that fork at
  `6a5ed04` (mace-sizing): PM files come from the PM line, shared/MACE files from the MACE line.
- **Fast-forward is impossible.** The PM deploy line and the MACE deploy line both descend from
  `6a5ed04`; neither tip is an ancestor of the other. A reconcile must **merge**, not FF.
- **The shared trio is the whole difficulty**: `main.py`, `agents/data_exec.py`,
  `brokers/robinhood.py` all diverge on the box. `brokers/base.py` does **not** (identical at
  every ref). Everything under `prediction_markets/` (plus `data/ufc_poly_kalshi_match.py`,
  which is PM-owned) is PM-alone.
- **A wholesale `main.py` overwrite once cost PM ~28h armed-but-not-trading** (the PM roster
  block was clobbered by a MACE branch). Merge `main.py` hunk-by-hunk, never wholesale.

---

## The two lines (common ancestor = `6a5ed04` "deploy(mace-sizing) BP-base+$400 floor, == box, config_hash 49476b0e")

### PM line — OURS, FF-able in isolation
```
prod-live b1c552b1 → 6a753b71 (ctx-pagination) → 312f8743 → 1373ccf8 → d19c0ab5 (UFC method)
```
- `d19c0ab5` **is** a descendant of prod-live `b1c552b1` (verified). 4 commits ahead, ALL
  PM-scoped.
- Branches: `pm-ufc-method-types-2026-09-12 @ d19c0ab5` (LOCAL-ONLY) — this is PM box truth;
  `pm-ctx-pagination-fix-2026-09-11 @ 0fcc9d3c` (LOCAL-ONLY).

### MACE line — THEIRS, NOT FF-able onto prod-live
```
6a5ed04 → 7683f59b (broker-failsafe rebased onto box-truth) → c2593e34 → f049468f → eb487c39 → ce5b748c (GDX time-exit cap)
```
- `ce5b748c` is **NOT** a descendant of prod-live `b1c552b1` — it descends from `6a5ed04`,
  which sits *below* `b1c552b1` on the PM line. `merge-base(d19c0ab5, ce5b748c) = 6a5ed04`.
- Branches: `mace-gdx-cap-boxbase-2026-09-11 @ ce5b748c` (LOCAL-ONLY) — MACE box truth
  (config_hash `bfde856f`); `platform-broker-failsafe-boxbase-2026-09-11 @ 8d2d3bba`
  (LOCAL-ONLY; a broker-failsafe branch — the reconcile should confirm its relationship to the
  `7683f59b` fork point that `ce5b748c` descends from).

---

## Per-file divergence — box vs prod-live `b1c552b1`, CR-stripped sha8

Box hashes read live 2026-09-12 03:08Z from `/home/azureuser/trading_corp`.
prod-live / PMtip(`d19c0ab5`) / MACEtip(`ce5b748c`) computed locally
(`git show <ref>:<path> | tr -d '\r' | sha256sum`). **Box matches the expected branch-tip hash
for every code+config file — zero graft drift.**

### PM-scoped (ours alone) — BOX == PM tip `d19c0ab5`
| file | prod-live | BOX (=PMtip) |
|---|---|---|
| trading_corp/prediction_markets/live_driver.py | d0c25810 | **6561b569** |
| trading_corp/prediction_markets/execution.py | 09c7e275 | **14787ba4** |
| trading_corp/data/ufc_poly_kalshi_match.py | 1a16b3aa | **ebabe6ce** |

(+ `tests/prediction_markets/test_ctx_pagination_fix.py`, `test_ufc_method_types.py`,
`reports/prediction_markets/UFC_METHOD_TYPES_DEPLOY_2026-09-12.md` — repo artifacts on the PM
branch; not runtime, not necessarily grafted to the box.)

### SHARED trio-plus — BOX == MACE tip `ce5b748c` (prod-live has the `6a5ed04` version)
| file | prod-live | BOX (=MACEtip) | note |
|---|---|---|---|
| trading_corp/main.py | 5908b5dc | **cbf4b928** | ⚠️ shared — hunk-merge only |
| trading_corp/agents/data_exec.py | ffeca7f7 | **fc8ab253** | shared |
| trading_corp/brokers/robinhood.py | ecf5457e | **a26b8d0c** | shared |
| trading_corp/brokers/base.py | e7c8eb28 | e7c8eb28 | **UNCHANGED** — not divergent |

### MACE-scoped — BOX == MACE tip `ce5b748c`
| file | prod-live | BOX (=MACEtip) |
|---|---|---|
| config/mace.yaml | 49476b0e | **bfde856f** (= GDX-cap config_hash) |
| trading_corp/mace/config.py | d5aeb365 | **43fabae5** |
| trading_corp/mace/execution.py | 847f0b83 | **073b6eed** |
| trading_corp/mace/manager.py | deb073d3 | **a28d4e63** |

(+ `tests/test_broker_connect_failsafe.py`, `tests/test_mace_execution.py`, `reports/mace/*`,
`_gdxcap_deploy/*` deploy tooling — MACE-line only.)

---

## Ancestry facts (confirmed from git, not repeated from memory)

| check | result |
|---|---|
| `6a5ed04` ancestor of prod-live `b1c552b1`? | **YES** (merge-base = 6a5ed04) — MACE-sizing is now IN prod-live |
| `6a5ed04` ancestor of `7220e32f`? | NO |
| `7220e32f` ancestor of `b1c552b1`? | YES — prod-live advanced 7220e32f → … → b1c552b1 |
| PM tip `d19c0ab5` descendant of prod-live? | YES (4 PM commits) |
| MACE tip `ce5b748c` descendant of prod-live? | **NO** (descends from 6a5ed04) |
| `merge-base(d19c0ab5, ce5b748c)` | **6a5ed04** (the fork point) |
| `95e78c4` ("deploy pm-cp3b") reachable? | **YES** — ancestor of prod-live + contained in 6 branches. NOT at risk. (Not an ancestor of `main` — expected; main has no PM code.) |
| `origin/main` prediction_markets file count | **0** |

**Interpretation:** the sizing gap the older memory flagged (vs `7220e32f`) has closed —
`6a5ed04` rode into prod-live via a later PM deploy. What remains **box-only vs prod-live** is
(a) the PM ctx+UFC line (`6a753b71…d19c0ab5`) and (b) the later MACE line
(`7683f59b` broker-failsafe + `…ce5b748c` GDX-cap). Because those two lines diverge at
`6a5ed04` and the box is their file-level union, **no single commit is the box** — FF cannot
represent it.

---

## Branch push state

**PUSHED (origin same-name branch exists, in sync):**
- `pm-live-fixes-2026-09-12` (this session's docs + UI WIP — advances with this handoff commit)
- `pm-farm-livewhale-2026-09-11 @ 25875fe7`

**LOCAL-ONLY (NOT on origin — the reconcile must not assume these are pushed):**
- `pm-ufc-method-types-2026-09-12 @ d19c0ab5` — **PM box truth**
- `pm-ctx-pagination-fix-2026-09-11 @ 0fcc9d3c`
- `pm-leg-independence-fix-2026-09-10 @ 88f7b664` (already an ancestor of prod-live via an earlier FF)
- `mace-gdx-cap-boxbase-2026-09-11 @ ce5b748c` — **MACE box truth**
- `platform-broker-failsafe-boxbase-2026-09-11 @ 8d2d3bba`

---

## What a reconcile must do (stated, NOT executed here)

1. Represent the box as a **merge** of the PM line (`d19c0ab5`) and the MACE line (`ce5b748c`)
   over their common base `6a5ed04` — never a fast-forward.
2. **Shared files** `main.py` / `agents/data_exec.py` / `brokers/robinhood.py`: three-way merge
   hunk-by-hunk. **Never wholesale-overwrite `main.py`** (the ~28h armed-but-not-trading
   incident). `brokers/base.py` needs nothing (identical everywhere).
3. PM-scoped files → take `d19c0ab5` versions. MACE-scoped files → take `ce5b748c` versions.
   The box already equals these (zero drift), so the box tree is a valid merge result to verify
   against.
4. Push the local-only branches (or the merge commit) so origin reflects box truth. Keep
   `95e78c4` reachable. Leave `main` untouched — PM→main is a separate **whole-subtree merge**
   (main has 0 PM files), done later, not part of the prod-live reconcile.
5. Post-merge, verify MACE `config_hash bfde856f` and PM schema head **21** survive, and that all
   30 armed sub-divisions stay armed + live.

## Provenance / re-run
Read-only runners (local, this session): `cc/pm_divergence_boxhash_ro.ps1` (+ `.sh`) — box CR-stripped
hashes; local git via the worktree at `cc-pm-live-fixes-wt`. Re-derivable at any time; nothing was
mutated to produce this.
