# Stage 1 — RUNG 2 (deploy gated code + pm_web restart) — EXECUTION RECORD (2026-08-27)

**Authorization (Jack, 2026-08-27):** "STAGE 1 RUNG 2 — AUTHORIZED. Deploy the gated code and restart pm_web.
This authorizes rung 2 ONLY. Rung 3 … remains UNAUTHORIZED … No poller run, no adjudicator run, no DB writes
beyond what the deploy itself requires (which is none)." **This rung CHANGES BEHAVIOUR on purpose** — the pinned
list stops borrowing from the completed-trade rollup and reads the (empty) paper basis. /farm byte-identical would
have been a FAILURE. **Executed clean; the substitution bug — the defect that started the rebuild — is now fixed
on LIVE.**

Branch: `prediction-markets-stage0-2026-08-26` @ **172e1f0** (box-scratch green @ 0dd8825).
Window: **16:51–16:58 UTC**, clear of the 03:20 cron. **No PM DB writes** (code-only deploy; restart's init_db is a
no-op at schema 9). File deploy as azureuser; the single restart as root via `az vm run-command`.

## DEPLOY SET — 4 files (nominal 6, 2 unchanged)
`git diff --name-only c77f618 172e1f0 -- trading_corp/` = exactly **db.py, farm.py, paper.py, scripts/pm_cli.py**.
- **5-vs-6 answer:** nominal unit is 6 (the 5 PM-package files + pm_cli). **Actual changed = 4.** `stats.py` and
  `__init__.py` are **git-blob-identical** between c77f618 and 172e1f0 (`6192d87`, `05decea`) → already == branch on
  the box; **not redeployed**. `pm_cli.py` **IS** in the set (Stage-1's `paper-rollup`/`analyze` subcommands + gamma
  re-base of `paper-adjudicate`). It is **not imported by pm_web** (web imports `stats, positions, names, farm,
  analyze`), so it is Rung-3 machinery and cannot affect the restart.
- **NEVER touched:** `persistence/db.py` (legacy). The web entry is `web/app.py` (uvicorn), **unchanged** c77f618↔172e1f0
  — the render layer is byte-identical; only `farm.farm_rows`' *data* changes.
- **Lineage note:** `c77f618` is the **prod-live path-checkout ledger tip**, NOT an ancestor of the working branch.
  The box runtime == prod-live c77f618's file content. Box blob-verified == c77f618 for all 6 pre-deploy.

## Custody / manifest / re-hash gate
| file | box pre (c77f618) | branch blob (172e1f0) | sha256 | bytes |
|---|---|---|---|---|
| db.py | cafaefb | **392e182** | 106e2b03…3815 | 38440 |
| farm.py | 7f3be8a | **b2c9ac9** | 22cbb575…a864 | 11166 |
| paper.py | 0f568eb | **7f6caea** | 539f4de2…a642 | 32031 |
| pm_cli.py | 0900182 | **9676f5c** | 39d73232…373d | 14017 |

Deploy: extract tar of the 4 files @172e1f0 → **manifest-assert** (scratch blob == branch) → **pre-place custody**
(box blob == c77f618, fail-fast, touched nothing) → **per-file code backup** → **place + `chmod 644`** → **re-hash
gate** (target blob == branch AND perms == 644, owner azureuser). **The tar-664 drift Jack flagged recurred**
(`git archive` emitted `-rw-rw-r--`); forced 644 and verified `-rw-r--r--` on all 4.
- **Code backup:** `~/pm_stage1_rung2_codebak_20260827T165514Z/` (db/farm/paper/pm_cli at c77f618 blobs). Rollback =
  restore these 4 + restart pm_web.

## Restart
`az vm run-command` (root, RG-SHARED-PROD/tc-prod-vm): `systemctl restart prediction-markets-web.service`.
- pm_web MainPID **652 → 13102** (changed), ExecMainStatus **0**, ActiveState active / SubState running, NRestarts 0
  (manual restart does not bump it). **/healthz 200**, **/farm 200**. Engine PID **676** unchanged.

## ★ THE SUBSTITUTION-BUG PROOF (before → after, captured via the box's own code)
BEFORE via c77f618 farm.py (borrowed pm_category_stats) → AFTER via 172e1f0 farm.py (pm_paper_category_stats, empty):
- **Aggregate:** pinned rows with a non-null borrowed `n_resolved`: **BEFORE 92/92 → AFTER 0/92.** Empty is CORRECT
  (rollup has not run — that is Rung 3).
- **Named pairs (unrecoverable BEFORE snapshot):**
  - mlb·SDTrading: `n_resolved=501 win_rate=0.944 roi=0.906 net=$4,434,001` → **all None**
  - ufc·4751346: `n_resolved=1307 win_rate=0.630 roi=0.085` → **all None**
  - all 10 mlb + 10 ufc pinned pairs flipped borrowed → None, and **all remain DISPLAYED** (LEFT JOIN honest-empty,
    not filtered out).
- **Live render:** /farm **182835 → 129578 bytes** (−53,257, −29%), **same 2597 lines** (rows kept, stat cells
  emptied). Borrowed net token `4434001` in the live HTML: **1 → 0**. Pair rows still render (SDTrading 6→6,
  MadeiraIsland 12→12 grep hits).

## ★ PROSPECTS UNCHANGED — the three bases stayed separate (§H checkpoint)
- Candidate/prospect rows: **0 → 0** (no Search yet; render path byte-identical `_ROWS_SQL_CANDIDATE`).
- Completed-lane data untouched: `pm_category_stats` named-pair **MD5 c2b7d926… == before**.
- `farm_summary` identical: n_pinned 92, n_candidates 0, states {88 polled_none_open, 4 polled_has_open}, unknown 0.
- Bases: **pinned → pm_paper_category_stats** (now empty), **candidate → pm_category_stats** (unchanged), live → P3
  (not built).

## No-write / schema invariants (post)
schema **9**, grace **259200**, pm_paper_trade **102**, pm_paper_category_stats **0 rows**, 15 cats / 92 pinned pairs,
candidate 0 — all identical to the pre-deploy baseline. Stage-1 gated queries execute clean against schema 9
(`farm_rows(PINNED)`, `farm_rows(CANDIDATE)`, direct `SELECT pm_paper_category_stats`).

## prod-live ledger (authored, NOT pushed)
Local `prod-live`: c77f618 → **570727b** (path-checkout of the 4 files from 172e1f0; blobs == box; parent c77f618,
so c77f618 remains an ancestor / fast-forward-only). **Not pushed — origin/prod-live still c77f618.** Advancing
origin/prod-live is a **separate authorization**.

## STILL UNAUTHORIZED
- **Rung 3** (poller/adjudicator cadence). Cadence RULED = path (b) start polling, `*/30`, order
  poll → adjudicate → rollup — "the ruling, not the go." No poller/adjudicator run.
- **Advancing origin/prod-live** to 570727b (separate authorization).
- Tier-2 poller categorization gap = separate ticket.

## Runners (cc\, pure ASCII)
`pm_rung2_discovery.sh` · `pm_rung2_before.sh` · `pm_stage1_rung2_deploy.sh` · `pm_rung2_after.sh` (+ deploy tar
`pm_rung2_deploy.tar`); restart via `az vm run-command`.
