# P1+P2 — Kalshi copy dashboard scope + labels — DEPLOY RUNBOOK (STAGED, NOT DEPLOYED)

**Status:** PACKAGE STAGED on `claude-2026-07-26`. **Awaiting operator deploy-go — do NOT deploy without it.**
**Why:** completes tonight's S2 fix (c). Fix (c) epoch-scoped the per-whale panel's *base* columns (`_query_pm_whales`) but the *intel* merge (`_query_kalshi_whale_intel`, called inside it) was missed — leaving `Copies/Copy%/Net PnL` **all-time** next to live-scoped `Resolved/WR%/Realized P&L` on the same Selected row. Since the September re-selection **sorts by `Net PnL`**, that column MUST be live-scoped or the decision ranks whales by the paper-era backlog. P2 fixes two stale labels the S2 fixes left behind.

## What ships (all committed to the branch; NOT applied to prod)
| fix | file | change | layer |
|---|---|---|---|
| **P1** intel epoch-scope | `trading_corp/web/data.py` `_query_kalshi_whale_intel` | keyword params `kalshi_copy_mode`/`kalshi_copy_epoch` + `_kalshi_copy_mode_clause` on all 4 component queries (copies/no_side/sports on `ts`; net-PnL on `entry_ts`); Selected caller (`_query_pm_whales` ~L4910) passes its existing mode/epoch. **Watch caller left default 'all'** (watch whales aren't live-copied; base cols are external Apify all-time — out of P1 scope, noted below) | web |
| **P2** header label | `web/templates/partials/pm_dashboard_body.html:665` | `Selected whales — paper performance` → mode-aware `— {{ view.wr_mode }} performance` for kalshi (poly unchanged) | template |
| **P2** copies tooltips | same template L715 (Selected) + L947 (Watch) | `"Our would_have_placed copies"` → `"Our copies: paper would_have_placed + live kalshi_copy_placed_live (side=buy)"` | template |

**md5 (LF, base→patched):** data.py `14eeb84b→636eeba8`; pm_dashboard_body.html `90750afe→e1b68f57`. **`ast.parse` OK** on data.py; template edits are simple `{% if %}`/attr changes (Jinja render-verified at step 6).

## Tests to apply/run at build (NOT edited — keeps suite green pre-build)
- `tests/test_kalshi_whale_intel.py`: add `test_intel_epoch_scopes_kalshi_copy` — seed copies (would_have_placed + placed_live), no_side skips, and round-trips ACROSS the epoch boundary; assert `_query_kalshi_whale_intel(..., kalshi_copy_mode='live', kalshi_copy_epoch=EPOCH)` counts only post-epoch rows (copies/no_side/net_pnl/n_resolved), and that default (mode='all') is byte-identical to pre-P1 (backward-compat). Run the full file.

## Deploy sequence (operator-gated, ONE restart)
1. **Drift-gate:** prod == base for both — `tr -d '\r' < prod-file | md5sum` must equal data.py `14eeb84b` (= the S2-deployed version) and template `90750afe`. Any mismatch → STOP.
2. **Stage:** scp LF-normalized patched files → `/tmp`; verify staged md5 == patched (`636eeba8` / `e1b68f57`).
3. **Backup + swap:** `~/trading_corp/.bak_p1p2_20260727/{web,web-templates}/`; swap into place.
4. **Restart** (web served in-process → restart bounces it) via Azure Run Command (root, no sudo). Confirm new PID, 0 new tracebacks, RH re-auth clean, all divisions load.
5. **Backfill:** none (P1+P2 are query/label changes only).
6. **Verify (both):**
   - **P1 — intel Net PnL now live-scoped (matches base scope).** Replicate the fixed intel query with the live epoch and confirm per-whale `n_resolved` collapses from all-time to the live subset, matching the base panel `Resolved`:
     `SELECT json_extract(extra_json,'$.whale_handle') h, count(*) n_resolved, round(sum(realized_pnl),2) gross FROM kalshi_round_trips WHERE division='kalshi_copy_trading' AND json_extract(extra_json,'$.whale_handle') IN ('AI.EDGE','MaggieTheEagle') AND entry_ts>='2026-07-01T14:08:58' GROUP BY h;`
     Expect **AI.EDGE n_resolved=10, Maggie=3** (was all-time 22/18) — matches the S2 (c) base `epoch_live` (10/3). Confirms intel now scopes with the base columns. (Optional stronger check: `curl` the live dashboard partial and confirm the rendered `Net PnL` cell equals the live net-of-fee, not the paper-inclusive all-time figure.)
   - **P2 — labels read correctly in live mode.** `curl -s "http://localhost:<port>/partials/prediction-markets/kalshi_copy_trading?wr_mode=live"` and grep: header contains **`— live performance`** (NOT "paper performance"); copies tooltip contains **`kalshi_copy_placed_live`**. Then `?wr_mode=paper` → header shows `— paper performance`.

## Rollback
- Restore `.bak_p1p2_20260727/{web/data.py, web/templates/partials/pm_dashboard_body.html}` + restart. Pure read-query + label changes; no data mutation, nothing to un-migrate.

## Notes / deferred
- **Watch bench intel stays all-time** (its caller `_query_kalshi_watch_only_rows` keeps default 'all'). This is intentional for P1 scope — watch whales aren't live-copied and their base columns are external Apify all-time, so the panel is internally consistent. If the operator later wants the Watch bench also live-scoped, that's a one-line change at the Watch caller (`~L5136`) — separate.
- **P3 (candidate flag, autopause shadow indicator, provisional badge) DEFERRED** to the September / n≥30 trigger — they render empty at current n<30, and the shadow indicator should ship alongside the flip-to-active decision. **PM-only features SKIPPED** (Analyze button, entry-price columns — not relevant to this use case).
