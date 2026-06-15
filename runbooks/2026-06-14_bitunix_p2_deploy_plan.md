# Bitunix P2 auto-book + latch-release — deploy plan (PREPARED; NOT executed)

**Prepared 2026-06-14 — read-only + draft only. NO deploy, NO restart, NO prod write this session.** All steps are **operator-gated**. Disclosure per `82fda13` at the bottom. Mirrors the P1 single-file md5-gate/backup/atomic-mv + restart pattern.

The P2 fix (branch `bitunix-p2-autobook-latch-release-2026-06-14`, commit `dbd9dcf`, reviewed) makes the engine **self-recover** after a server-side close: auto-book a confirmed `missing_on_broker` row at the known stop level (flagged estimate) + release `_halt_new_orders` on two consecutive clean ticks. Report: `reports/2026-06-14_bitunix_p2_autobook_latch.md`.

---

## 1. What deploys — ONE file
| | |
|---|---|
| **File (live engine)** | `trading_corp/agents/divisions/bitunix_position_reconciler.py` **only** |
| `bitunix.py` | **UNCHANGED** vs the deployed P1 (confirmed by `git diff main...dbd9dcf` — P2 touches only the reconciler `.py` + tests/report/BACKLOG, which don't deploy) |
| `config/strategies.yaml` | **NOT deployed** (holds prod-only `execution_mode: live`, line 1022 — a whole-file replace would revert Bitunix to PAPER) |

Prod path: `/home/azureuser/trading_corp/trading_corp/agents/divisions/bitunix_position_reconciler.py`.

## 2. Pre-flight — verified read-only 2026-06-14 ~23:28 UTC
| check | result | status |
|---|---|---|
| Prod reconciler md5 (current base) | `64f33e76934e754c76437e6ce7d7d290` | ✓ **== main `299b40c` == the deployed P1 target** → base INCLUDES P1, no drift |
| P2 target md5 (`dbd9dcf`, LF) | `ae2fbc74895d5b4341f0d2d0804579c1` | the version to install |
| `execution_mode` (line 1022) | `execution_mode: live` | ✓ must survive restart |
| kalshi (line 1645) | `enabled: false` | ✓ must survive restart |
| Engine | PID **2721839**, active, started **22:12:02 UTC** (the trade-2 restart; **no bounce since**) | ✓ |
| Current halt state | reconciler ticks 23:22–23:24 are **clean** (`reconciled`, miss=0/orph=0), BUT `_halt_new_orders` is still **LATCHED** from the manual-short divergence (22:24–22:34) — the current P1 code never releases it on a clean tick (the exact bug P2 fixes). **→ the engine is currently halted from new entries.** | ⚠ |

> md5s are **LF-normalized git-blob** values (comparable to prod's LF file). The apply script `tr -d '\r'` normalizes a CRLF-staged file before gating.

**Note — the deploy+restart does double duty:** it both **clears the stuck latch** (so the engine resumes after the manual-short episode) AND installs the self-recovery so future stop-outs don't re-strand.

## 3. Deploy + restart (operator-gated; nothing executed here)

### 3a. Apply — `runbooks/deploy_apply_p2.sh`
Per file: `tr -d '\r'` → **Gate A** (prod == base `64f33e76…`) → **Gate B** (staged == target `ae2fbc74…`) → py_compile → **backup** `…/bitunix_position_reconciler.py.bak-pre-p2autobook-2026-06-14` (rollback = restore + restart) → atomic mv → chown → re-verify == target. Aborts on any mismatch. **No restart in the script; `strategies.yaml` excluded.**
1. Stage: `git show dbd9dcf:trading_corp/agents/divisions/bitunix_position_reconciler.py > /tmp/…` then scp to prod `/tmp/p2/`.
2. Stream the apply script; confirm `OK applied` + the backup path.

### 3b. Restart — AFTER the apply (clears the latch + loads the fix)
`sudo systemctl restart trading-corp` (NOPASSWD). Re-inits the broker (`_halt_new_orders` → False) and loads the new reconciler. `strategies.yaml` is untouched → `execution_mode: live` + kalshi `enabled: false` are re-read on boot.

## 4. Post-restart verification (read-only)
1. `systemctl status` — new PID, active, `NRestarts=0`; ExecStart still `--live --brokers bitunix`.
2. Prod reconciler md5 == **target** `ae2fbc74…`; fresh `.pyc` mtime > proc start.
3. **`execution_mode: live` PRESERVED** — startup audit `mode: LIVE`, `Registered bitunix_futures … (paper=False)`; kalshi `enabled: false` preserved (lines 1022/1645 unchanged).
4. **Not halted** — fresh boot starts `_halt_new_orders=False`; with the account flat + reconciler clean it stays clear → engine takes new entries. (Reconciler sanity-poll has a ~12–15 min post-boot startup delay, as with P1/trade-2 — the first clean `position_state_reconciled` lands ~+14 min.)
5. **New self-recovery behavior present** — a `position_state_halt_released` audit appears the next time a divergence resolves to two consecutive clean ticks; `auto_book_server_side_close` audits appear on a server-side close (see §5).

## 5. Live-behavior check — confirm on the NEXT real stop-out (the trade-2-style proof)
This is the thing to watch (P1 was confirmed live by trade 2). On the next bot **stop-out**, the deployed fix should, with NO manual intervention:
1. `missing_on_broker` for the closed row appears (1st tick) → confirmed (2nd tick) → **`auto_book_server_side_close`** audit → the row books `result='loss'` at the stop level, flagged `auto_booked_from_stop_level` / `pnl_basis='known_level_estimate'` / `slippage_unreconciled=true` (PnL is a KNOWN-LEVEL ESTIMATE — it will differ from the real fill by the slippage, e.g. trade 2's ~138pt; exit fee left unset for later true-up).
2. Next ticks go **clean** (`position_state_reconciled`, miss=0/orph=0).
3. After two consecutive clean ticks → **`position_state_halt_released`** → `_halt_new_orders` clears → engine **self-resumes** (no manual book, no restart).
A `filled_legs`-non-empty (partial-TP) or no-stop close instead **defers** (`auto_book_deferred`, row stays NULL+flagged) — operator books those manually; the engine stays halted on them by design.

Read-only confirmation queries (operator/agent): `auto_book_server_side_close` / `position_state_halt_released` audit rows for the order, and the row's `result` + `result_source`.

## 6. Out of scope / follow-up
The **accurate signed-fetch-of-real-fill** auto-book (exact price/PnL/fee, no slippage estimate) supersedes the known-level estimate — filed BACKLOG (P2), motivated by trade 2's ~138pt slippage. Separate (it's a signed/public-API call).

## Disclosure (82fda13)
Agent: read-only SSH (`md5sum`/`grep`/`systemctl show`/`sqlite3 -readonly` reads) + local git/source review + local file writes (this plan + the apply script) + local git commit on the deploy-prep branch. **No deploy, no restart, no prod write, no signed/public-API call.** `config/strategies.yaml` not touched. Branch `bitunix-p2-deploy-prep-2026-06-14`, UNMERGED — for operator review and execution.
